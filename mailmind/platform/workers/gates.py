"""The gate each account's live-IMAP work passes.

Every job that talks to an account's mail server runs through `run`, which runs it with that
account's gate held, so no two jobs talk to one account at once. A job runs on the caller's own
thread, so what that thread carries (its context variables, its database connections) stays with
it, which is why a job is a function and not a block.

There are two kinds of gate, chosen by MAILMIND_WORKERS:

- unset (or `global`): one lock is every account's gate, as `server._ops_lock` was. A slow or
  unreachable account holds up every other, and the priority is only recorded.
- `account`: each account has a gate of its own, so one account's slow server holds up only its
  own jobs. Jobs waiting for a gate go in priority order, then in the order they came. A job that
  passes EVERY_ACCOUNT waits until no gate is held, and holds them all; while it waits, a job
  that would come after it waits too, so it is never starved.

A job that spans accounts, or touches no one account's server, passes EVERY_ACCOUNT. Standard
library only: python3 server.py uses it.
"""
import enum
import itertools
import os
import threading
import time
from collections.abc import Callable

EVERY_ACCOUNT = "*"


class Priority(enum.IntEnum):
    """Which job runs first when several wait for one gate."""
    INTERACTIVE = 0       # the user waits on it: open a body, file a message, undo, list folders
    AFTER_USER = 1        # set going by the user, done after: queued moves, a send's Sent copy
    SYNC = 2              # bringing a folder up to date
    MAINTENANCE = 3       # reconcile, body backfill, prefetch, expiry


class Busy(Exception):
    """The gate was held, and the job was asked not to wait for it, or not past its deadline."""


def per_account():
    """Whether each account has a gate of its own (MAILMIND_WORKERS=account)."""
    return os.environ.get("MAILMIND_WORKERS", "").strip().lower() == "account"


def _refuse_nesting(wait):
    if wait and threading.get_ident() in _holding:
        raise RuntimeError("a job waited on a gate from inside a job")


# ── the one lock ───────────────────────────────────────────────────────────────────────────────

_lock = threading.Lock()


def _run_global(account, fn, wait, deadline):
    if not _lock.acquire(blocking=wait, timeout=deadline if wait and deadline is not None else -1):
        raise Busy(account)
    me = threading.get_ident()
    _holding[me] = _holding.get(me, 0) + 1
    try:
        return fn()
    finally:
        _drop(me)
        _lock.release()


# ── a gate for each account ────────────────────────────────────────────────────────────────────

_cv = threading.Condition()
_held = {}                # account (or EVERY_ACCOUNT) -> the thread holding its gate
_waiting = []             # [(priority, arrival), account] of each job waiting for a gate
_arrival = itertools.count()
_holding = {}             # thread -> how many gates it holds: a job may not wait inside a job


def _drop(me):
    if _holding.get(me, 0) <= 1:
        _holding.pop(me, None)
    else:
        _holding[me] -= 1


def _free(account):
    if account == EVERY_ACCOUNT:
        return not _held
    return account not in _held and EVERY_ACCOUNT not in _held


def _ahead(key, account):
    """Whether a job waiting before `key` must go first: one for the same account, or one for
    every account, and for an EVERY_ACCOUNT job, any."""
    return any(k < key and (a == account or EVERY_ACCOUNT in (a, account)) for k, a in _waiting)


def _admissible(key, account):
    return _free(account) and not _ahead(key, account)


def _run_per_account(account, fn, priority, wait, deadline):
    me = threading.get_ident()
    with _cv:
        key = (int(priority), next(_arrival))
        if not _admissible(key, account):
            if not wait:
                raise Busy(account)
            entry = [key, account]
            _waiting.append(entry)
            ends = None if deadline is None else time.monotonic() + deadline
            try:
                while not _admissible(key, account):
                    left = None if ends is None else ends - time.monotonic()
                    if left is not None and left <= 0:
                        raise Busy(account)
                    _cv.wait(left)
            finally:
                _waiting.remove(entry)
                _cv.notify_all()          # a job behind this one may go now
        _held[account] = me
        _holding[me] = _holding.get(me, 0) + 1
    try:
        return fn()
    finally:
        with _cv:
            del _held[account]
            _drop(me)
            _cv.notify_all()


# ── the façade ─────────────────────────────────────────────────────────────────────────────────

def run[T](account: str, fn: Callable[[], T], *, priority: int, wait: bool = True, deadline: float | None = None) -> T:
    """Run fn() with `account`'s gate held, and return what it returns. The gate comes free when
    fn returns or raises. With wait=False a held gate raises Busy at once, and fn doesn't run;
    with a `deadline` (seconds), a gate not had by then raises Busy, and fn doesn't run.

    A job never waits on a gate itself: one that would raises RuntimeError rather than wait
    forever on its own thread."""
    Priority(priority)
    _refuse_nesting(wait)
    if per_account():
        return _run_per_account(account, fn, priority, wait, deadline)
    return _run_global(account, fn, wait, deadline)


def held(account: str = EVERY_ACCOUNT) -> bool:
    """Whether `account`'s gate is held now, by any job. For EVERY_ACCOUNT: whether any is."""
    if not per_account():
        return _lock.locked()
    with _cv:
        return bool(_held) if account == EVERY_ACCOUNT else not _free(account)
