"""Wiring for the legacy modules at the repo root.

Each front door calls `start(server)` before it serves, and the tests' app fixture calls
`wire(server)`, so all of them put the app together the same way. This is the one module in the
package that imports legacy modules; tests/legacy_ratchet.txt lists each import.
"""
import threading

from mailmind.accounts.domain import events as account_events
from mailmind.bootstrap import compose, mailbox
from mailmind.compose.api import DraftCopyRemoved, Failed, Sent
from mailmind.platform import bus, profile
from mailmind.platform.workers import scheduler

from mailmind.bootstrap.mailbox import offline
from mailmind.bootstrap.mailbox import realtime
from mailmind.bootstrap.mailbox import store
import summarize
from mailmind.mailbox.domain.events import Applied
from mailmind.mailbox.domain.events import Restored
from mailmind.mailbox.domain.events import FlagsChanged
from mailmind.mailbox.domain.events import InboxChanged


_wired = []                             # wire()'s subscriptions, each a function that ends it


def wire(server):
    """Subscribe the server to what the legacy workers publish on mailmind.platform.bus: a send
    that went out or an attempt that failed, a queued move applied and one that won't be refresh the
    stats and tell the open tabs, and IDLE's
    news syncs those accounts. An account added, changed or removed brings IDLE's workers in line
    (realtime.reconcile, which does nothing until IDLE starts). Wiring again replaces the last
    wiring. Starts nothing.

    It takes the server module as an argument rather than importing it: `python3 server.py` runs
    as __main__, and an import here would load a second copy with state of its own."""
    unwire()
    _wired.extend([bus.subscribe(Sent, server._after_send),
                   bus.subscribe(Failed, server._after_send),
                   bus.subscribe(DraftCopyRemoved, server._after_draft_removed),
                   bus.subscribe(Applied, mailbox.filing.settled),
                   bus.subscribe(Restored, mailbox.filing.settled),
                   bus.subscribe(InboxChanged, server._after_idle),
                   bus.subscribe(FlagsChanged, server._after_flags_changed)])
    _wired.extend(bus.subscribe(topic, realtime.reconcile) for topic in
                  (account_events.AccountAdded, account_events.AccountChanged, account_events.AccountRemoved))


def unwire():
    """End wire()'s subscriptions."""
    while _wired:
        _wired.pop()()


def server_module():
    """The legacy server module, for a front door other than server.py itself. Imported here, at
    call time: server imports this module, and run as __main__ it would load a second copy."""
    import server
    return server


def _daemon(target):
    threading.Thread(target=target, name=getattr(target, "__name__", None), daemon=True).start()


def start(server, spawn=_daemon):
    """Everything a front door does before it serves: check the profile, create or migrate the
    schema, wire the workers, and start them: the two queues' workers, the background jobs on a
    scheduler (server.schedule says which), and IDLE. Read-only, only snooze wake starts, since it
    needs no mail server: sends and moves wait in their queues, and nothing syncs.

    `spawn(target)` runs each worker and each job's lane; by default on a daemon thread of its own."""
    profile.check()                      # a MAILMIND_PROFILE that names no profile stops the start
    store.init()
    wire(server)
    jobs = scheduler.Scheduler()
    server.schedule(jobs)
    server.JOBS = jobs                   # what /api/debug/jobs reports
    if server.nosync():
        jobs.start(spawn)
        print("(auto-sync disabled: the read-only profile writes nothing, so sends and moves wait "
              "in their queues; snooze still runs)", flush=True)
    else:
        spawn(compose.outbox.worker_loop)
        spawn(offline.worker_loop)
        jobs.start(spawn)
        realtime.start()                     # per-account IMAP IDLE workers + SSE dispatch
    print(f"[ai] {summarize.describe_engine()}", flush=True)
    print(f"[imap] {mailbox.describe()}", flush=True)


def assess_batch() -> int:
    """`python -m mailmind assess --batch`: bring in a batch that has ended, and send what's waiting as the
    next. The app's assess job does the same for a backlog; this asks for any size. 0 when it could."""
    if not profile.writes_allowed():
        print("The read-only profile sends nothing out, so nothing was sent.")
        return 1
    store.init()
    if summarize.engine() == "none":
        print("AI is off: there is no API key, so nothing was sent.")
        return 1
    summarize.batch_collect(print)
    sent = summarize.batch_submit(at_least=1, keep=0, log=print)
    status = summarize.batch_status()
    if status["waiting"]:
        print(f"{status['waiting']} messages are out in a batch. Most end within the hour, and all within a day; "
              "the app brings the answers in when it next runs, or run this again.")
    elif not sent:
        print("Nothing was sent: no message is waiting, or today's AI limit has no room.")
    return 0
