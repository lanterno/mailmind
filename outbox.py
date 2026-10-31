"""Durable outbound queue + a single drain worker for MailMind.

A send is enqueued (state=queued) by the server after compose.build_message has already
validated it; the worker claims it (queued->sending atomically), delivers over SMTP under
the shared ops lock, then does the best-effort post-send side effects (APPEND to Sent for
non-Gmail, set \\Answered on the original for replies — each guarded by a done-once flag),
and marks it sent. Failures back off exponentially and retry; after MAX_ATTEMPTS the row
is 'error' (terminal until an explicit retry). Exactly-once delivery is enforced by a
persisted `delivered` flag set the instant the wire send returns: a crash/DB-failure after
delivery requeues the row, but send_one() then skips the send and only finishes bookkeeping.
The Message-ID + raw bytes are built once and reused, so the Sent copy matches the wire.
"""
import json
import time
import threading
import contextlib
from datetime import datetime, timezone

import store
import accounts
import smtpsend

MAX_ATTEMPTS = 5
_BACKOFF_BASE = 20      # secs; doubles per attempt
_BACKOFF_CAP = 900
_POLL = 30             # worker wakes at least this often to catch due retries
SEND_DELAY = 10        # grace window before the wire send, so Undo Send can cancel it

_kick = threading.Event()
_ops_lock = contextlib.nullcontext()
_after_send = None


def configure(ops_lock=None, after_send=None):
    """Wire the worker to the server's shared IMAP/SMTP lock + a post-send callback."""
    global _ops_lock, _after_send
    if ops_lock is not None:
        _ops_lock = ops_lock
    _after_send = after_send


def _now():
    return int(time.time())


def enqueue(built, meta):
    """Persist a queued send. `built` = compose.build_message(...) output; `meta` =
    {account, idempotency_key, in_reply_to_pk, subject, to, cc, bcc}. Returns
    (outbox_id, created). Kicks the worker."""
    row = {
        "account": meta["account"],
        "idempotency_key": meta["idempotency_key"],
        "message_id": built["message_id"],
        "in_reply_to_pk": meta.get("in_reply_to_pk"),
        "envelope": json.dumps(built["envelope"]),
        "recipients": json.dumps({"to": built["to_disp"], "cc": built["cc_disp"],
                                  "bcc": built["bcc_disp"]}),
        "subject": meta.get("subject") or "",
        "raw": built["raw"],
        "draft_id": meta.get("draft_id"),
        # grace window OR a future scheduled time (send-later) — outbox_due gates on this, so
        # scheduling needs ZERO worker changes. A queued future row is still Cancel-able.
        "next_attempt_at": max(_now() + SEND_DELAY, int(meta.get("send_after") or 0)),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    c = store.conn()
    try:
        oid, created = store.outbox_insert(c, row)
        c.commit()
    finally:
        c.close()
    kick()
    return oid, created


def cancel(oid):
    """Undo Send — drop the row if it's still in its grace window. False if too late."""
    c = store.conn()
    try:
        ok = store.outbox_cancel(c, oid)
        c.commit()
        return ok
    finally:
        c.close()


def kick():
    _kick.set()


def retry(oid):
    c = store.conn()
    try:
        ok = store.outbox_requeue(c, oid)
        c.commit()
    finally:
        c.close()
    if ok:
        kick()
    return ok


def list_recent(limit=50):
    c = store.conn()
    try:
        rows = store.outbox_list(c, limit)
    finally:
        c.close()
    out = []
    for r in rows:
        rcpt = {}
        try:
            rcpt = json.loads(r["recipients"]) or {}
        except Exception:
            pass
        na = r["next_attempt_at"] or 0
        scheduled = r["state"] == "queued" and na > _now() + SEND_DELAY + 5   # clearly future, not just the grace window
        out.append({
            "id": r["id"], "account": r["account"], "subject": r["subject"] or "(no subject)",
            "to": rcpt.get("to", []), "cc": rcpt.get("cc", []), "bcc_count": len(rcpt.get("bcc", [])),
            "state": r["state"], "attempts": r["attempts"], "error": r["last_error"],
            "created_at": r["created_at"], "sent_at": r["sent_at"],
            "next_attempt_at": na, "scheduled": scheduled,
        })
    return {"ok": True, "items": out}


def send_now(oid):
    """Bring a scheduled send forward to now (Send-now from the outbox flyout)."""
    c = store.conn()
    try:
        ok = store.outbox_send_now(c, oid, _now())
        c.commit()
    finally:
        c.close()
    if ok:
        kick()
    return ok


def _commit(fn):
    """Run a single store mutation on a short-lived connection, best-effort."""
    c = store.conn()
    try:
        fn(c); c.commit()
    finally:
        c.close()


def _side_effects(oid, row, acct):
    """Best-effort, done-once: Sent copy (non-Gmail) + \\Answered on the original.
    Each IMAP op grabs _ops_lock briefly (NOT held across the whole thing). A failure
    here never fails an already-delivered send — it's logged + skipped."""
    if smtpsend.provider_saves_sent(acct) and not row["sent_appended"]:
        _commit(lambda c: store.outbox_set(c, oid, sent_appended=1))   # Gmail auto-saves
    elif not row["sent_appended"]:
        try:
            with _ops_lock:
                smtpsend.append_to_sent(acct, row["raw"])
            _commit(lambda c: store.outbox_set(c, oid, sent_appended=1))
        except Exception as e:
            print(f"[outbox] Sent-copy failed for #{oid}: {smtpsend.sanitize_smtp_error(e)}", flush=True)
    if row["in_reply_to_pk"] and not row["answered_set"]:
        try:
            import mailops
            with _ops_lock:
                mailops.set_flags(row["in_reply_to_pk"], {"answered": True})
            _commit(lambda c: store.outbox_set(c, oid, answered_set=1))
        except Exception as e:
            print(f"[outbox] \\Answered failed for #{oid}: {type(e).__name__}", flush=True)
    # sent-no-reply: seed a follow-up on the replied-to thread (replies only — a brand-new compose
    # has no local thread_id until its Sent copy syncs). Best-effort, done-once, never fails the send.
    if row["in_reply_to_pk"] and not row["followup_recorded"]:
        try:
            c = store.conn()
            orig = store.get_message(c, row["in_reply_to_pk"])
            tid = (orig["thread_id"] if orig else None)
            if tid:
                store.followup_record_sent(c, row["account"], tid, row["in_reply_to_pk"],
                                           _now(), datetime.now(timezone.utc).isoformat())
            c.commit(); c.close()
            _commit(lambda c: store.outbox_set(c, oid, followup_recorded=1))
        except Exception as e:
            print(f"[outbox] followup record failed for #{oid}: {type(e).__name__}", flush=True)
    # the draft this message was composed from is superseded once it's truly sent
    if row["draft_id"] and not row["draft_deleted"]:
        try:
            # accounts/smtpsend are module-level imports — a local `import` here would shadow
            # them as function-locals and break every earlier reference with UnboundLocalError
            c = store.conn(); dr = store.draft_get(c, row["draft_id"]); c.close()
            if dr:
                with _ops_lock:          # the row goes with its copy: Edit re-checks it under this lock
                    smtpsend.delete_draft_uid(accounts.get(dr["account"]), dr["folder"],
                                              dr["uid"], dr["uidvalidity"])
                    _commit(lambda c: store.draft_delete(c, row["draft_id"]))
            _commit(lambda c: store.outbox_set(c, oid, draft_deleted=1))
        except Exception as e:
            print(f"[outbox] draft cleanup failed for #{oid}: {type(e).__name__}", flush=True)


def send_one(oid):
    c = store.conn()
    try:
        claimed = store.outbox_claim(c, oid)
        c.commit()
        if not claimed:
            return
        row = store.outbox_get(c, oid)
    finally:
        c.close()
    if not row:
        return
    try:
        acct = accounts.get(row["account"])  # host/port/creds ONLY from config (no SSRF)
    except KeyError:
        _commit(lambda c: store.outbox_release(c, oid, "account removed", row["attempts"] + 1, 0, True))
        return
    # Deliver exactly once. A recovered row (delivered=1, requeued by reset_stuck) skips
    # the wire send and only finishes bookkeeping — so a crash after send never re-sends.
    if not row["delivered"]:
        try:
            smtpsend.send(acct, row["raw"], json.loads(row["envelope"]))  # lock-free: SMTP != IMAP ops
        except Exception as e:
            err = smtpsend.sanitize_smtp_error(e)
            attempts = row["attempts"] + 1
            terminal = attempts >= MAX_ATTEMPTS
            delay = min(_BACKOFF_CAP, _BACKOFF_BASE * (2 ** (attempts - 1)))
            _commit(lambda c: store.outbox_release(c, oid, err, attempts, _now() + delay, terminal))
            print(f"[outbox] send #{oid} failed ({err}); "
                  f"{'giving up' if terminal else f'retry in {delay}s'}", flush=True)
            return
        _commit(lambda c: store.outbox_mark_delivered(c, oid))  # commit BEFORE side effects
    # delivered — best-effort side effects, then mark sent (all guarded so a DB hiccup
    # can't strand a delivered row; next pass sees delivered=1 and only retries bookkeeping)
    try:
        _side_effects(oid, row, acct)
        _commit(lambda c: store.outbox_mark_sent(c, oid, datetime.now(timezone.utc).isoformat()))
        if _after_send:
            _after_send()
        print(f"[outbox] sent #{oid}", flush=True)
    except Exception as e:
        print(f"[outbox] post-send bookkeeping for #{oid} deferred: {type(e).__name__}", flush=True)


def drain():
    c = store.conn()
    try:
        due = store.outbox_due(c, _now())
    finally:
        c.close()
    for oid in due:
        try:
            send_one(oid)
        except Exception as e:   # one bad row must not abort the rest of the batch
            print(f"[outbox] send_one #{oid} errored: {type(e).__name__}: {e}", flush=True)


def worker_loop():
    # recover anything left mid-send by a crash
    c = store.conn(); store.outbox_reset_stuck(c); c.commit(); c.close()
    while True:
        _kick.wait(timeout=_POLL)
        _kick.clear()
        try:
            drain()
        except Exception as e:
            print(f"[outbox] drain error: {type(e).__name__}: {e}", flush=True)
