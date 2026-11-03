"""Wiring for the legacy modules at the repo root.

`python3 server.py` and the tests' app fixture both call `wire(server)` before anything runs, so
the two put the app together the same way.
"""
import outbox
import readmutq
import realtime
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
