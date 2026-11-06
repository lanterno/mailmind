"""Optional persistent IMAP connection pool for the READ hot path (cold body fetch ~37s on
Proton → ~2s warm). Default OFF: with MAILMIND_POOL unset, borrow() is byte-for-byte today's
behavior (fresh connect + logout per op), so landing this changes nothing until explicitly enabled.

A borrow yields a MailSession (mailmind.mailbox.application.session), opened through
mailmind.bootstrap.mailbox like any other.

SAFETY (load-bearing):
  • Only read-only paths (ingest.fetch_body, the folder rail's counts, a sweep's plan) borrow.
    MUTATIONS NEVER touch the pool — they keep opening a fresh session — so a pooled session can
    never carry a stale selected folder into a UID STORE/MOVE/EXPUNGE. All mutation invariants
    (UIDVALIDITY, never-bulk-EXPUNGE, etc.) are untouched.
  • Validate-on-take: a cached connection is NOOP'd before reuse; on any failure / age / use-count
    limit it is discarded and a guaranteed-fresh connection is opened. So an idle server-side drop is
    transparent. A mid-borrow error discards the slot and propagates exactly as a fresh connection
    would today.
  • One connection per account (slot), guarded by a per-account lock taken INSIDE the caller's
    IMAP gate (mailmind.platform.workers.gates; canonical order → no new deadlock). imaplib is not
    thread-safe per connection; the lock guarantees a pooled connection is never shared concurrently.
  • Proton Bridge (loopback/STARTTLS) has a low per-account connection cap; one pooled conn per
    account satisfies it, and we validate every borrow. Enable per-account via the env list.

Enable:  MAILMIND_POOL=1            → all accounts
         MAILMIND_POOL=a@x.com,b@y → only those accounts (Phase-1: one Gmail)
"""
import contextlib
import os
import threading
import time
from collections.abc import Generator
from typing import Any, TypedDict

from mailmind.mailbox.application.ports import Account, Accounts, OpenSession
from mailmind.mailbox.application.session import MailSession

# A pooled connection is discarded after ProviderProfile.recycle_seconds (600, well under a
# server's ~29-minute idle drop; 90 on Bridge) or recycle_uses (200; 40 on Bridge).


class _Slot(TypedDict):
    M: MailSession | None
    lock: threading.Lock
    born: float
    uses: int


def _targets() -> str:
    return os.environ.get("MAILMIND_POOL", "").strip()


def _logout(M: MailSession) -> None:
    try:
        M.logout()
    except Exception:
        pass


def _valid(M: MailSession) -> bool:
    """A cached session is reusable only if it answers NOOP OK."""
    try:
        return M.noop()
    except Exception:
        return False


class Pool:
    def __init__(self, accounts: Accounts, open_session: OpenSession):
        self._accounts = accounts
        self._open_session = open_session
        self._reg_lock = threading.Lock()
        self._slots: dict[str, _Slot] = {}
        self._stats = {"created": 0, "reused": 0, "validate_fail": 0, "discard_dirty": 0}

    def _enabled(self, acct: Account) -> bool:
        v = _targets()
        if not v:
            return False
        if v.lower() in ("1", "all", "on", "true", "yes"):
            return not self._accounts.profile(acct).bridge   # "all" = every account EXCEPT the fragile Bridge
        return acct["email"] in {x.strip() for x in v.split(",") if x.strip()}   # explicit list honored verbatim


    def _slot(self, email: str) -> _Slot:
        with self._reg_lock:
            s: _Slot | None = self._slots.get(email)
            if s is None:
                s = {"M": None, "lock": threading.Lock(), "born": 0.0, "uses": 0}
                self._slots[email] = s
            return s


    def _take(self, slot: _Slot, acct: Account) -> tuple[MailSession, bool]:
        """Return a ready connection: a validated cached one, or a fresh login. Caller holds slot lock.
        Proton Bridge (loopback) is fragile + connection-capped, so a pooled Bridge connection is
        recycled far more aggressively (shorter age, fewer reuses) and still validated every borrow."""
        p = self._accounts.profile(acct)
        max_age, max_uses = p.recycle_seconds, p.recycle_uses
        held = slot["M"]
        if held is not None:
            too_old = (time.time() - slot["born"]) > max_age or slot["uses"] >= max_uses
            if not too_old and _valid(held):
                slot["uses"] += 1
                self._stats["reused"] += 1
                return held, True
            self._stats["validate_fail"] += 1 if not too_old else 0
            _logout(held)
            slot["M"] = None
        fresh = self._open_session(acct)    # guaranteed-fresh, authenticated, no folder selected
        slot["M"] = fresh
        slot["born"] = time.time()
        slot["uses"] = 1
        self._stats["created"] += 1
        return fresh, False


    @contextlib.contextmanager
    def borrow(self, acct: Account) -> Generator[MailSession]:
        """Yield a MailSession for a READ-ONLY operation. Dormant (env unset) → a fresh session, logged
        out after. Enabled → a validated pooled session that is repooled on a clean exit and discarded
        on any error."""
        if not self._enabled(acct):
            fresh = self._open_session(acct)
            try:
                yield fresh
            finally:
                _logout(fresh)
            return
        slot = self._slot(acct["email"])
        with slot["lock"]:
            pooled, _reused = self._take(slot, acct)
            try:
                yield pooled
            except BaseException:
                _logout(pooled)           # poison the slot on ANY error; next borrow reconnects fresh
                slot["M"] = None
                self._stats["discard_dirty"] += 1
                raise


    def stats(self) -> dict[str, Any]:
        with self._reg_lock:                        # serialize against self._slot() insertion / self.close_all()
            live: dict[str, dict[str, Any]] = {}
            for e, s in self._slots.items():
                m = s["M"]                      # snapshot once → consistent open/age
                live[e] = {"open": m is not None, "uses": s["uses"],
                           "age": round(time.time() - s["born"], 1) if m is not None else None}
        return {"enabled": bool(_targets()), "target": _targets(), "counters": dict(self._stats), "slots": live}


    def close_all(self) -> None:
        """Best-effort teardown of all pooled connections (e.g. on shutdown)."""
        with self._reg_lock:
            for s in self._slots.values():
                if s["M"] is not None:
                    _logout(s["M"])
                    s["M"] = None
