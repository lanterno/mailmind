"""The gate each account's live-IMAP work passes.

Every job that talks to an account's mail server runs through `run`, which runs it with that
account's gate held, so no two jobs talk to one account at once. For now a single lock is every
account's gate, as `server._ops_lock` was, so a slow account holds up the rest. A later phase
gives each account a worker of its own that runs its jobs in priority order; the callers stay as
they are, which is why a job is a function and not a block: it may run on another thread.

A job that spans accounts, or touches no one account's server, passes EVERY_ACCOUNT. Standard
library only: python3 server.py uses it.
"""
import enum
import threading

EVERY_ACCOUNT = "*"


class Priority(enum.IntEnum):
    """Which job runs first when several wait for one gate. For now a gate is a lock, and the
    priority is only recorded at the call site."""
    INTERACTIVE = 0       # the user waits on it: open a body, file a message, undo, list folders
    AFTER_USER = 1        # set going by the user, done after: queued moves, a send's Sent copy
    SYNC = 2              # bringing a folder up to date
    MAINTENANCE = 3       # reconcile, body backfill, prefetch, expiry


class Busy(Exception):
    """The gate was held, and the job was asked not to wait for it."""


_lock = threading.Lock()
_holder = None            # the thread running a job, while one runs


def run(account, fn, *, priority, wait=True):
    """Run fn() with `account`'s gate held, and return what it returns. The gate comes free when
    fn returns or raises. With wait=False a held gate raises Busy at once, and fn doesn't run.

    A job never waits on a gate itself: one that would raises RuntimeError rather than wait
    forever on its own thread."""
    global _holder
    Priority(priority)
    if wait and _holder == threading.get_ident():
        raise RuntimeError("a job waited on a gate from inside a job")
    if not _lock.acquire(blocking=wait):
        raise Busy(account)
    _holder = threading.get_ident()
    try:
        return fn()
    finally:
        _holder = None
        _lock.release()


def held(account=EVERY_ACCOUNT):
    """Whether `account`'s gate is held now, by any job."""
    return _lock.locked()
