"""A gate for each account (MAILMIND_WORKERS=account), so one account's slow server holds up only its
own jobs. `gates` is what the rest of the app calls: it picks this or its one lock.

Jobs waiting for a gate go in priority order, then in the order they came. A job that passes
EVERY_ACCOUNT waits until no gate is held, and holds them all; while it waits, a job that would come
after it waits too, so it is never starved. A job runs on its caller's own thread.

Standard library only: python3 server.py uses it.
"""
import itertools
import threading
import time
from collections.abc import Callable

EVERY_ACCOUNT = "*"


class Busy(Exception):
    """The gate was held, and the job was asked not to wait for it, or not past its deadline."""


# ── who holds a gate, of either kind ───────────────────────────────────────────────────────────

_holding: dict[int, int] = {}     # thread -> how many gates it holds: a job may not wait inside a job


def refuse_nesting(wait: bool) -> None:
    if wait and threading.get_ident() in _holding:
        raise RuntimeError("a job waited on a gate from inside a job")


def took(me: int) -> None:
    _holding[me] = _holding.get(me, 0) + 1


def gave_back(me: int) -> None:
    if _holding.get(me, 0) <= 1:
        _holding.pop(me, None)
    else:
        _holding[me] -= 1


# ── a gate for each account ────────────────────────────────────────────────────────────────────

_cv = threading.Condition()
_held: dict[str, int] = {}                  # account (or EVERY_ACCOUNT) -> the thread holding its gate
_waiting: list[tuple[tuple[int, int], str]] = []   # ((priority, arrival), account) of each job waiting for a gate
_arrival = itertools.count()


def _free(account: str) -> bool:
    if account == EVERY_ACCOUNT:
        return not _held
    return account not in _held and EVERY_ACCOUNT not in _held


def _ahead(key: tuple[int, int], account: str) -> bool:
    """Whether a job waiting before `key` must go first: one for the same account, or one for
    every account, and for an EVERY_ACCOUNT job, any."""
    return any(k < key and (a == account or EVERY_ACCOUNT in (a, account)) for k, a in _waiting)


def _admissible(key: tuple[int, int], account: str) -> bool:
    return _free(account) and not _ahead(key, account)


def run[T](account: str, fn: Callable[[], T], priority: int, wait: bool, deadline: float | None) -> T:
    me = threading.get_ident()
    with _cv:
        key = (int(priority), next(_arrival))
        if not _admissible(key, account):
            if not wait:
                raise Busy(account)
            entry = (key, account)
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
        took(me)
    try:
        return fn()
    finally:
        with _cv:
            del _held[account]
            gave_back(me)
            _cv.notify_all()


def held(account: str) -> bool:
    with _cv:
        return bool(_held) if account == EVERY_ACCOUNT else not _free(account)
