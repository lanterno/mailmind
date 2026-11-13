"""The Python server as the API suite (tests/api) runs it: the app `python -m mailmind serve` builds, in a process of its own,
behind the line protocol every server under that suite speaks. A server written in another language brings a launcher of
its own that speaks the same protocol; the suite knows nothing else about it.

The suite starts it with these in its environment, beside MAILMIND_HOME and MAILMIND_DB_KEY:

    MAILMIND_TEST_NOW       the time its clock stands at, frozen (ISO 8601)
    MAILMIND_TEST_DIST      the build of the page it serves
    MAILMIND_TEST_PORT      the loopback port to listen on, when the suite restarts it; else any free one
    MAILMIND_TEST_GUARDED   folders whose data files the tripwire refuses, besides the repo root (os.pathsep between)

It writes `ready <port>` on standard output once it answers, then reads one command a line from standard input and answers
each with one line:

    clock <ISO 8601>    ok: its clock stands at that time from now on
    work <job> <JSON>   ok once one pass of a background job has run, given the arguments in the JSON list; else
                        `failed <why>`. The jobs: outbox (send what is due), commands (carry queued changes to the mail
                        servers), drafts (put saved drafts that are due on their mail servers), snoozes (wake what is
                        due), follow-ups (note replies and expiries), assess (triage mail AI hasn't summarized), sync
                        (one folder: ["<account>", "<folder>"]), reconcile (the ghost pruner's round over one account,
                        which yields to a user's change as it would: ["<account>"]), tidy (file the day's
                        low-priority mail, for someone who turned Tidy by itself on)
    settle              ok once work the requests started has ended; `busy <what>` after 30 seconds
    check               ok, or `problems <JSON list>`: what its own checks refused since the last check
    (end of input)      it stops and exits

Test hooks live here and never in the app: the frozen clock, the tripwire and the schema checks are this program's.
The app runs as `serve` runs it, without the workers that would sync and sweep on their own, so an answer depends only on
what the suite sends: requests, and a worker's pass when it says (`work`).
"""
import json
import os
import sys
import threading
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.dirname(HERE)
ROOT = os.path.dirname(TESTS)
sys.path[:0] = [HERE, TESTS, ROOT]

import guard                                        # noqa: E402
guard.install()                                     # before any MailMind module asks for a secret or opens a file
for _folder in filter(None, os.environ.get("MAILMIND_TEST_GUARDED", "").split(os.pathsep)):
    guard.protect(_folder)

# Threads that wait for the next request rather than doing work one started.
IDLE = ("app-http", "AnyIO worker")


def settle(before, timeout=30):
    """Wait for the threads requests started (and any they start meanwhile); what is still running after `timeout`."""
    deadline = time.monotonic() + timeout
    while running := [t for t in threading.enumerate()
                      if t not in before and t is not threading.current_thread() and not t.name.startswith(IDLE)]:
        if time.monotonic() >= deadline:
            return [t.name for t in running]
        for t in running:
            # One a request is starting at this moment is listed before it has started, and can't be joined until then.
            t._started.wait(max(0.0, deadline - time.monotonic()))
            t.join(max(0.0, deadline - time.monotonic()))
    return []


def jobs():
    """A pass of each background job a worker runs, by the name the protocol gives it."""
    from mailmind.bootstrap import accounts, assistant, attention, background
    from mailmind.bootstrap.compose import drafts, outbox
    from mailmind.bootstrap.mailbox import commands, ingest, reconciler
    return {"outbox": outbox.drain, "commands": commands.drain, "drafts": drafts.push_round,
            "snoozes": attention.snoozing.wake_due, "follow-ups": attention.followups.tick, "assess": assistant.assessments.round,
            "sync": lambda account, folder: ingest.sync_folder(accounts.get(account), folder),
            "reconcile": lambda account: reconciler.auto_reconcile_one(accounts.get(account)),
            "tidy": background.tidy_round}


def main():
    import dataclasses

    # The protocol keeps standard output to itself: what the app prints or logs goes to standard error, which the suite
    # keeps in the server's log.
    protocol = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    import pytest
    import time_machine

    traveller = time_machine.travel(datetime.fromisoformat(os.environ["MAILMIND_TEST_NOW"]), tick=False)
    clock = traveller.start()

    import app_client
    import schema_contract
    from mailmind.bootstrap import assistant, wiring
    from mailmind.platform import settings, webroot

    webroot.DIST = os.environ["MAILMIND_TEST_DIST"]
    patches = pytest.MonkeyPatch()
    schema_contract.watch_reads(patches)
    wiring.assemble()
    wiring.wire()                     # as wiring.start does, without starting the workers' threads
    assistant.governor().forget()     # someone here at the frozen time, not at the real time the process began
    running = app_client.RunningAsgiApp(port=int(os.environ.get("MAILMIND_TEST_PORT") or 0)).start()
    try:
        with settings.use(dataclasses.replace(settings.current(), port=running.port)):
            before = set(threading.enumerate())
            print(f"ready {running.port}", file=protocol, flush=True)
            for line in sys.stdin:
                command, _, argument = line.strip().partition(" ")
                if command == "clock":
                    clock.move_to(datetime.fromisoformat(argument))
                    answer = "ok"
                elif command == "work":
                    job, _, given = argument.partition(" ")
                    try:
                        jobs()[job](*json.loads(given or "[]"))
                        answer = "ok"
                    except Exception as e:                  # the suite fails the test with it
                        answer = f"failed {job}: {type(e).__name__}: {e}"
                elif command == "settle":
                    busy = settle(before)
                    answer = "busy " + ", ".join(busy) if busy else "ok"
                elif command == "check":
                    found = [*schema_contract.violations, *guard.violations]
                    schema_contract.violations.clear()
                    guard.violations.clear()
                    answer = "problems " + json.dumps(found) if found else "ok"
                else:
                    answer = f"unknown command {command!r}"
                print(answer, file=protocol, flush=True)
    finally:
        running.stop()
        wiring.unwire()
        patches.undo()
        traveller.stop()


if __name__ == "__main__":
    main()
