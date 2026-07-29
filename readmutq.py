"""Offline-safe read-mutation drain worker — a structural clone of outbox.py for read-side
mailbox mutations (trash/archive/junk/move/flag). When the route enqueues a mutation (because
IMAP was unreachable), this worker re-applies it once the server is back, exactly once.

Mirrors the outbox machinery 1:1: a single worker thread, atomic claim (queued→running), the
`applied` flag as the exactly-once gate (= outbox.delivered), exponential backoff, reset-stuck on
boot. The ONE difference from the outbox: the primary op here IS an IMAP op, so it runs under the
shared _ops_lock (the outbox's SMTP send is lock-free).
"""
import time
import threading
import contextlib
from datetime import datetime, timezone

import store
import mailops

MAX_ATTEMPTS = 5
_BACKOFF_BASE = 20      # secs; doubles per attempt
_BACKOFF_CAP = 900
_POLL = 30

_kick = threading.Event()
_ops_lock = contextlib.nullcontext()
_after_apply = None


def configure(ops_lock=None, after_apply=None):
    global _ops_lock, _after_apply
    if ops_lock is not None:
        _ops_lock = ops_lock
    _after_apply = after_apply


def _now():
    return int(time.time())


def kick():
    _kick.set()


def _commit(fn):
    c = store.conn()
    try:
        fn(c); c.commit()
    finally:
        c.close()


def _commit_get(mid):
    c = store.conn()
    try:
        return store.read_mut_get(c, mid)
    finally:
        c.close()


def _local_mirror(row):
    """Best-effort, idempotent: apply the same local DB change the inline path makes, keyed by
    Message-ID (NOT the stale payload pk). A move-type op drops the local row; a flag op sets flags.
    Re-running is harmless (delete of a gone row is a no-op; set_flags is idempotent)."""
    c = store.conn()
    try:
        r = store.get_message_by_msgid(c, row["account"], row["message_id"]) if row["message_id"] else None
        pk = r["pk"] if r else None
        if pk is None:
            return
        if row["op"] == "flag":
            import json
            flags = (json.loads(row["payload"] or "{}").get("flags") or {})
            store.set_local_flags(c, pk, **{k: int(bool(v)) for k, v in flags.items()})
        else:
            store.delete_message(c, pk)
        c.commit()
    finally:
        c.close()


def apply_one(mid):
    c = store.conn()
    try:
        claimed = store.read_mut_claim(c, mid)
        c.commit()
        if not claimed:
            return                       # lost the claim (cancelled / another worker) — mirror outbox
        row = store.read_mut_get(c, mid)
    finally:
        c.close()
    if not row:
        return
    # exactly-once: a recovered row (applied=1, requeued by reset_stuck) skips the IMAP op and only
    # finishes bookkeeping, so a crash after the op can never re-move/re-expunge.
    if not row["applied"]:
        try:
            with _ops_lock:                                  # IMAP op holds the shared lock (unlike SMTP)
                fresh = _commit_get(mid)                      # re-read under the lock: an undo may have
                if fresh is None:                            #   cancelled (deleted) the row between claim and here
                    return
                mailops.apply_queued_mutation(fresh)
        except mailops._UNREACHABLE as e:
            attempts = row["attempts"] + 1
            terminal = attempts >= MAX_ATTEMPTS
            delay = min(_BACKOFF_CAP, _BACKOFF_BASE * (2 ** (attempts - 1)))
            _commit(lambda c: store.read_mut_release(c, mid, type(e).__name__, attempts, _now() + delay, terminal))
            print(f"[readmutq] #{mid} unreachable ({type(e).__name__}); "
                  f"{'giving up' if terminal else f'retry {delay}s'}", flush=True)
            return
        _commit(lambda c: store.read_mut_mark_applied(c, mid))   # commit BEFORE the local mirror
    try:
        _local_mirror(row)
        _commit(lambda c: store.read_mut_mark_done(c, mid, datetime.now(timezone.utc).isoformat()))
        if _after_apply:
            _after_apply()
        print(f"[readmutq] applied #{mid} {row['op']}", flush=True)
    except Exception as e:
        print(f"[readmutq] post-apply bookkeeping for #{mid} deferred: {type(e).__name__}", flush=True)


def drain():
    c = store.conn()
    try:
        due = store.read_mut_due(c, _now())
    finally:
        c.close()
    for mid in due:
        try:
            apply_one(mid)
        except Exception as e:           # one bad row never aborts the batch
            print(f"[readmutq] apply_one #{mid} errored: {type(e).__name__}: {e}", flush=True)


def worker_loop():
    c = store.conn(); store.read_mut_reset_stuck(c); c.commit(); c.close()   # recover crash-interrupted rows
    while True:
        _kick.wait(timeout=_POLL)
        _kick.clear()
        try:
            drain()
        except Exception as e:
            print(f"[readmutq] drain error: {type(e).__name__}: {e}", flush=True)
