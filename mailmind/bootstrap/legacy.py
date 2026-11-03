"""Wiring for the legacy modules at the repo root.

Each front door calls `start(server)` before it serves, and the tests' app fixture calls
`wire(server)`, so all of them put the app together the same way. This is the one module in the
package that imports legacy modules; tests/legacy_ratchet.txt lists each import.
"""
import threading

import outbox
import readmutq
import realtime
import store
import summarize
import summarize_thread


def wire(server):
    """Connect the legacy workers to the server: the outbox, the offline read-mutation queue and
    the thread summarizer share its IMAP lock and report back through its callbacks, and the IDLE
    layer syncs through it. Starts nothing.

    It takes the server module as an argument rather than importing it: `python3 server.py` runs
    as __main__, and an import here would load a second copy with a lock of its own."""
    outbox.configure(ops_lock=server._ops_lock, after_send=server._after_send)
    readmutq.configure(ops_lock=server._ops_lock, after_apply=server._after_readmut)
    summarize_thread.configure(ops_lock=server._ops_lock)
    realtime.configure(sync_handler=server.sync_guarded)


def server_module():
    """The legacy server module, for a front door other than server.py itself. Imported here, at
    call time: server imports this module, and run as __main__ it would load a second copy."""
    import server
    return server


def _daemon(target):
    threading.Thread(target=target, daemon=True).start()


def start(server, spawn=_daemon):
    """Everything a front door does before it serves: create or migrate the schema, wire the
    workers, and start them. The outbox, the offline queue and snooze wake run even under
    MAILMIND_NOSYNC, so queued sends still go out; the syncs, IDLE and the periodic jobs don't.

    `spawn(target)` runs each worker loop; by default on a daemon thread of its own."""
    store.init()
    wire(server)
    spawn(outbox.worker_loop)
    spawn(readmutq.worker_loop)
    spawn(server.snooze_tick)
    if server.nosync():
        print("(auto-sync disabled via MAILMIND_NOSYNC — serving read-only; outbox+snooze still run)", flush=True)
    else:
        spawn(server.startup)
        spawn(server.sync_loop)
        spawn(server.body_backfill_tick)     # make old mail full-text searchable
        spawn(server.draft_ahead_tick)       # top-K reply pre-gen (AI-polite)
        spawn(server.reconcile_loop)         # periodic ghost prune (MAILMIND_RECONCILE_SECS)
        spawn(server.expire_loop)            # age-out expiry rules (MAILMIND_EXPIRE_SECS)
        realtime.start()                     # per-account IMAP IDLE workers + SSE dispatch
    print(f"[ai] {summarize.describe_engine()}", flush=True)
