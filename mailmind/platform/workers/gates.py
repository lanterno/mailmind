"""The gate each account's live-IMAP work passes.

Every job that talks to an account's mail server runs through `run`, which runs it with that
account's gate held, so no two jobs talk to one account at once. A job runs on the caller's own
thread, so what that thread carries (its context variables, its database connections) stays with
it, which is why a job is a function and not a block.

There are two kinds of gate, chosen by MAILMIND_WORKERS:

- unset (or `global`): one lock is every account's gate, as `server._ops_lock` was. A slow or
  unreachable account holds up every other, and the priority is only recorded.
- `account`: each account has a gate of its own, in `account_worker`, so one account's slow server
  holds up only its own jobs.

A job that spans accounts, or touches no one account's server, passes EVERY_ACCOUNT. Standard
library only: python3 server.py uses it.
"""
import enum
import os
import threading
from collections.abc import Callable

from mailmind.platform.workers import account_worker
from mailmind.platform.workers.account_worker import EVERY_ACCOUNT, Busy


class Priority(enum.IntEnum):
    """Which job runs first when several wait for one gate."""
    INTERACTIVE = 0       # the user waits on it: open a body, file a message, undo, list folders
    AFTER_USER = 1        # set going by the user, done after: queued moves, a send's Sent copy
    SYNC = 2              # bringing a folder up to date
    MAINTENANCE = 3       # reconcile, body backfill, prefetch, expiry


def per_account():
    """Whether each account has a gate of its own (MAILMIND_WORKERS=account)."""
    return os.environ.get("MAILMIND_WORKERS", "").strip().lower() == "account"


# ── the one lock ───────────────────────────────────────────────────────────────────────────────

_lock = threading.Lock()


def _run_global(account, fn, wait, deadline):
    if not _lock.acquire(blocking=wait, timeout=deadline if wait and deadline is not None else -1):
        raise Busy(account)
    me = threading.get_ident()
    account_worker.took(me)
    try:
        return fn()
    finally:
        account_worker.gave_back(me)
        _lock.release()


# ── the façade ─────────────────────────────────────────────────────────────────────────────────

def run[T](account: str, fn: Callable[[], T], *, priority: int, wait: bool = True, deadline: float | None = None) -> T:
    """Run fn() with `account`'s gate held, and return what it returns. The gate comes free when
    fn returns or raises. With wait=False a held gate raises Busy at once, and fn doesn't run;
    with a `deadline` (seconds), a gate not had by then raises Busy, and fn doesn't run.

    A job never waits on a gate itself: one that would raises RuntimeError rather than wait
    forever on its own thread."""
    Priority(priority)
    account_worker.refuse_nesting(wait)
    if per_account():
        return account_worker.run(account, fn, priority, wait, deadline)
    return _run_global(account, fn, wait, deadline)


def held(account: str = EVERY_ACCOUNT) -> bool:
    """Whether `account`'s gate is held now, by any job. For EVERY_ACCOUNT: whether any is."""
    if not per_account():
        return _lock.locked()
    return account_worker.held(account)
