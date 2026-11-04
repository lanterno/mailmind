"""Wiring for the legacy modules at the repo root.

Each front door calls `start(server)` before it serves, and the tests' app fixture calls
`wire(server)`, so all of them put the app together the same way. This is the one module in the
package that imports legacy modules; tests/legacy_ratchet.txt lists each import.
"""
import threading

from mailmind.accounts.domain import events as account_events
from mailmind.platform import bus, profile
from mailmind.platform.workers import scheduler

import outbox
import readmutq
import realtime
import store
import summarize


_wired = []                             # wire()'s subscriptions, each a function that ends it


def wire(server):
    """Subscribe the server to what the legacy workers publish on mailmind.platform.bus: a send
    that went out and a queued move applied refresh the stats and tell the open tabs, and IDLE's
    news syncs those accounts. An account added, changed or removed brings IDLE's workers in line
    (realtime.reconcile, which does nothing until IDLE starts). Wiring again replaces the last
    wiring. Starts nothing.

    It takes the server module as an argument rather than importing it: `python3 server.py` runs
    as __main__, and an import here would load a second copy with state of its own."""
    unwire()
    _wired.extend([bus.subscribe(outbox.Sent, server._after_send),
                   bus.subscribe(readmutq.Applied, server._after_readmut),
                   bus.subscribe(realtime.InboxChanged, server._after_idle)])
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
        spawn(outbox.worker_loop)
        spawn(readmutq.worker_loop)
        jobs.start(spawn)
        realtime.start()                     # per-account IMAP IDLE workers + SSE dispatch
    print(f"[ai] {summarize.describe_engine()}", flush=True)
