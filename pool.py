"""Optional persistent IMAP connection pool for the READ hot path (cold body fetch ~37s on
Proton → ~2s warm). Default OFF: with MAILMIND_POOL unset, borrow() is byte-for-byte today's
behavior (fresh connect + logout per op), so landing this changes nothing until explicitly enabled.

SAFETY (load-bearing):
  • Only the read-only path (ingest.fetch_body: readonly SELECT + BODY.PEEK) borrows. MUTATIONS
    NEVER touch the pool — they keep calling accounts.connect() fresh — so a pooled connection can
    never carry a stale SELECTed folder into a UID STORE/MOVE/EXPUNGE. All mutation invariants
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
import os
import time
import threading
import contextlib

import accounts

MAX_AGE = 600        # seconds; discard a pooled conn older than this (well under ~29-min server idle drop)
MAX_USES = 200       # discard after this many reuses

_reg_lock = threading.Lock()
_slots = {}          # email -> {"M": conn|None, "lock": Lock, "born": float, "uses": int}
_stats = {"created": 0, "reused": 0, "validate_fail": 0, "discard_dirty": 0}


def _targets():
    return os.environ.get("MAILMIND_POOL", "").strip()


def _is_bridge(acct):
    """Proton Bridge (loopback + STARTTLS) has a low per-account connection cap and is fragile —
    excluded from the broad catch-all; only an explicit per-email opt-in (Phase 2) may pool it."""
    return acct.get("kind") == "proton" or (acct.get("host") == "127.0.0.1" and acct.get("starttls"))


def _enabled(acct):
    v = _targets()
    if not v:
        return False
    if v.lower() in ("1", "all", "on", "true", "yes"):
        return not _is_bridge(acct)        # "all" = every account EXCEPT the fragile Bridge
    return acct["email"] in {x.strip() for x in v.split(",") if x.strip()}   # explicit list honored verbatim


def _slot(email):
    with _reg_lock:
        s = _slots.get(email)
        if s is None:
            s = {"M": None, "lock": threading.Lock(), "born": 0.0, "uses": 0}
            _slots[email] = s
        return s


def _logout(M):
    try:
        M.logout()
    except Exception:
        pass


def _valid(M):
    """A cached connection is reusable only if it answers NOOP OK."""
    try:
        typ, _ = M.noop()
        return typ == "OK"
    except Exception:
        return False


def _take(slot, acct):
    """Return a ready connection: a validated cached one, or a fresh login. Caller holds slot lock.
    Proton Bridge (loopback) is fragile + connection-capped, so a pooled Bridge connection is
    recycled far more aggressively (shorter age, fewer reuses) and still validated every borrow."""
    bridge = _is_bridge(acct)
    max_age = 90 if bridge else MAX_AGE
    max_uses = 40 if bridge else MAX_USES
    M = slot["M"]
    if M is not None:
        too_old = (time.time() - slot["born"]) > max_age or slot["uses"] >= max_uses
        if not too_old and _valid(M):
            slot["uses"] += 1
            _stats["reused"] += 1
            return M, True
        _stats["validate_fail"] += 1 if not too_old else 0
        _logout(M)
        slot["M"] = None
    M = accounts.connect(acct)        # guaranteed-fresh, authenticated, no folder selected
    slot["M"] = M
    slot["born"] = time.time()
    slot["uses"] = 1
    _stats["created"] += 1
    return M, False


@contextlib.contextmanager
def borrow(acct):
    """Yield an IMAP connection for a READ-ONLY operation. Dormant (env unset) → fresh connect+logout,
    identical to calling accounts.connect() directly. Enabled → a validated pooled connection that is
    repooled on a clean exit and discarded on any error."""
    if not _enabled(acct):
        M = accounts.connect(acct)
        try:
            yield M
        finally:
            _logout(M)
        return
    slot = _slot(acct["email"])
    with slot["lock"]:
        M, _reused = _take(slot, acct)
        try:
            yield M
        except BaseException:
            _logout(M)                # poison the slot on ANY error; next borrow reconnects fresh
            slot["M"] = None
            _stats["discard_dirty"] += 1
            raise


def stats():
    with _reg_lock:                        # serialize against _slot() insertion / close_all()
        live = {}
        for e, s in _slots.items():
            m = s["M"]                      # snapshot once → consistent open/age
            live[e] = {"open": m is not None, "uses": s["uses"],
                       "age": round(time.time() - s["born"], 1) if m is not None else None}
    return {"enabled": bool(_targets()), "target": _targets(), "counters": dict(_stats), "slots": live}


def close_all():
    """Best-effort teardown of all pooled connections (e.g. on shutdown)."""
    with _reg_lock:
        for s in _slots.values():
            if s["M"] is not None:
                _logout(s["M"])
                s["M"] = None
