"""Wiring for the legacy modules at the repo root.

Each front door calls `start(server)` before it serves, and the tests' app fixture calls
`wire(server)`, so all of them put the app together the same way. This is the one module in the
package that imports legacy modules; tests/legacy_ratchet.txt lists each import.
"""
import threading

from mailmind.platform import profile

import outbox
import readmutq
import realtime
import store
import summarize


def wire(server):
    """Connect the legacy workers to the server: the outbox and the offline read-mutation queue
    report back through its callbacks, and the IDLE layer syncs through it. Starts nothing. (Each
    takes the IMAP gate itself, from mailmind.platform.workers.gates.)

    It takes the server module as an argument rather than importing it: `python3 server.py` runs
    as __main__, and an import here would load a second copy with a lock of its own."""
    outbox.configure(after_send=server._after_send)
    readmutq.configure(after_apply=server._after_readmut)
    realtime.configure(sync_handler=server.sync_guarded)


def server_module():
    """The legacy server module, for a front door other than server.py itself. Imported here, at
    call time: server imports this module, and run as __main__ it would load a second copy."""
    import server
    return server


def _daemon(target):
    threading.Thread(target=target, daemon=True).start()


def start(server, spawn=_daemon):
    """Everything a front door does before it serves: check the profile, create or migrate the
    schema, wire the workers, and start them. Read-only, only snooze wake starts, since it needs
    no mail server: sends and moves wait in their queues, and nothing syncs.

    `spawn(target)` runs each worker loop; by default on a daemon thread of its own."""
    profile.check()                      # a MAILMIND_PROFILE that names no profile stops the start
    store.init()
    wire(server)
    if server.nosync():
        spawn(server.snooze_tick)
        print("(auto-sync disabled: the read-only profile writes nothing, so sends and moves wait "
              "in their queues; snooze still runs)", flush=True)
    else:
        spawn(outbox.worker_loop)
        spawn(readmutq.worker_loop)
        spawn(server.snooze_tick)
        spawn(server.startup)
        spawn(server.sync_loop)
        spawn(server.body_backfill_tick)     # make old mail full-text searchable
        spawn(server.draft_ahead_tick)       # top-K reply pre-gen (AI-polite)
        spawn(server.reconcile_loop)         # periodic ghost prune (MAILMIND_RECONCILE_SECS)
        spawn(server.expire_loop)            # age-out expiry rules (MAILMIND_EXPIRE_SECS)
        realtime.start()                     # per-account IMAP IDLE workers + SSE dispatch
    print(f"[ai] {summarize.describe_engine()}", flush=True)
