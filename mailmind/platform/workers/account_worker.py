"""What each account's server gets of its own: a gate, and a connection kept between jobs.

A gate for each account (MAILMIND_WORKERS=account) means one account's slow server holds up only its
own jobs. `gates` is what the rest of the app calls: it picks this or its one lock. Jobs waiting for
a gate go in priority order, then in the order they came. A job that passes EVERY_ACCOUNT waits until
no gate is held, and holds them all; while it waits, a job that would come after it waits too, so it
is never starved. A job runs on its caller's own thread.

KeptConnections holds the connection, and ConnectionPolicy says how long one may serve. They know
nothing of what a connection is: the caller says how to open, check and close one, and its provider
gives the numbers.

Standard library only: python3 server.py uses it.
"""
import contextlib
import itertools
import threading
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any

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


# ── a connection kept for each account ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ConnectionPolicy:
    """How long a kept connection may serve before it is replaced."""
    max_age: float                # seconds since it was opened
    max_uses: int                 # borrows

    def spent(self, age: float, uses: int) -> bool:
        return age > self.max_age or uses >= self.max_uses


class _Slot[C]:
    def __init__(self) -> None:
        self.conn: C | None = None
        self.lock = threading.Lock()
        self.born = 0.0
        self.uses = 0


class KeptConnections[C]:
    """One connection kept for each key, lent to one borrower at a time.

    A kept connection is checked before each reuse. One its policy calls spent, or that fails the
    check, is closed and a new one opened, so a borrower never sees one the far end dropped while it
    sat. A borrow that raises closes it, and the next borrow opens a new one. A slot's lock is taken
    inside the caller's gate, never around one.

    `check` says whether a kept connection still answers, and `close` raises nothing."""

    def __init__(self, check: Callable[[C], bool], close: Callable[[C], None]):
        self._check = check
        self._close = close
        self._reg_lock = threading.Lock()
        self._slots: dict[str, _Slot[C]] = {}
        self._stats = {"created": 0, "reused": 0, "validate_fail": 0, "discard_dirty": 0}

    def _slot(self, key: str) -> _Slot[C]:
        with self._reg_lock:
            s = self._slots.get(key)
            if s is None:
                s = _Slot[C]()
                self._slots[key] = s
            return s

    def _take(self, slot: _Slot[C], policy: ConnectionPolicy, open_new: Callable[[], C]) -> C:
        """A ready connection: the kept one if it may serve and still answers, else a new one. The
        caller holds the slot's lock."""
        held = slot.conn
        if held is not None:
            spent = policy.spent(time.time() - slot.born, slot.uses)
            if not spent and self._check(held):
                slot.uses += 1
                self._stats["reused"] += 1
                return held
            self._stats["validate_fail"] += 1 if not spent else 0
            self._close(held)
            slot.conn = None
        fresh = open_new()
        slot.conn = fresh
        slot.born = time.time()
        slot.uses = 1
        self._stats["created"] += 1
        return fresh

    @contextlib.contextmanager
    def lend(self, key: str, policy: ConnectionPolicy, open_new: Callable[[], C]) -> Generator[C]:
        """Yield `key`'s connection. It stays kept on a clean exit, and is closed on any error."""
        slot = self._slot(key)
        with slot.lock:
            conn = self._take(slot, policy, open_new)
            try:
                yield conn
            except BaseException:
                self._close(conn)         # poison the slot on ANY error; the next borrow opens a new one
                slot.conn = None
                self._stats["discard_dirty"] += 1
                raise

    def counters(self) -> dict[str, int]:
        return dict(self._stats)

    def slots(self) -> dict[str, dict[str, Any]]:
        """How each key's slot stands."""
        with self._reg_lock:              # serialize against self._slot() insertion / self.close_all()
            live: dict[str, dict[str, Any]] = {}
            for key, s in self._slots.items():
                conn = s.conn             # snapshot once → consistent open/age
                live[key] = {"open": conn is not None, "uses": s.uses,
                             "age": round(time.time() - s.born, 1) if conn is not None else None}
        return live

    def close_all(self) -> None:
        """Best-effort teardown of all kept connections (e.g. on shutdown)."""
        with self._reg_lock:
            for s in self._slots.values():
                if s.conn is not None:
                    self._close(s.conn)
                    s.conn = None
