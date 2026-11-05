"""Durable outbound queue + a single drain worker for MailMind.

A send is enqueued (state=queued) by the server after compose.build_message has already
validated it; the worker claims it (queued->sending atomically), delivers over SMTP, holding
no IMAP gate, then does the best-effort post-send side effects (APPEND to Sent for non-Gmail,
set \\Answered on the original for replies — each guarded by a done-once flag, and each a job
on its account's IMAP gate),
and marks it sent. The queue is a DurableQueue over the outbox table: failures back off
exponentially and retry; after MAX_ATTEMPTS the row is 'error' (terminal until an explicit
retry). Exactly-once delivery is enforced by a persisted `delivered` flag, the queue's gate,
set the instant the wire send returns: a crash/DB-failure after delivery requeues the row, but
send_one() then skips the send and only finishes bookkeeping.
A Sent copy that fails puts the delivered row back in the queue, with the same back-off and
attempts counted afresh, so the copy is filed once the server is back; the gate keeps the retry
from sending again. Listed, such a row is `sent`, or `error` with a Retry once the copy gives up.
The Message-ID + raw bytes are built once and reused, so the Sent copy matches the wire.
"""
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import store
import accounts
import smtpsend
from mailmind.accounts.domain import provider
from mailmind.mailbox.domain import roles as folder_roles
from mailmind.platform import bus, profile
from mailmind.platform.workers import gates
from mailmind.platform.workers.durable_queue import DurableQueue

MAX_ATTEMPTS = 5
_BACKOFF_BASE = 20      # secs; doubles per attempt
_BACKOFF_CAP = 900
_POLL = 30             # worker wakes at least this often to catch due retries
SEND_DELAY = 10        # grace window before the wire send, so Undo Send can cancel it

# each account's sends in a lane of their own: one waiting on its IMAP server for a Sent copy
# holds up no other account's
QUEUE = DurableQueue("outbox", "outbox", running="sending", finished="sent", finished_at="sent_at",
                     gate="delivered", connect=lambda: store.conn(), max_attempts=MAX_ATTEMPTS,
                     backoff_base=_BACKOFF_BASE, backoff_cap=_BACKOFF_CAP, poll=_POLL, lane="account")


SENT_COPY_PENDING = "Delivered; its copy in Sent is still to be saved"
SENT_COPY_FAILED = "Delivered, but its copy couldn't be saved in Sent"


@dataclass(frozen=True)
class Sent:
    """A queued send went out, or a later try at its Sent copy ended (published on
    mailmind.platform.bus)."""
    outbox_id: int
    account: str


@dataclass(frozen=True)
class DraftCopyRemoved:
    """A sent message's draft left the server and the list (published on mailmind.platform.bus)."""
    account: str
    folder: str


@dataclass(frozen=True)
class Failed:
    """An attempt at a queued send failed: it waits for its next try, or, `gave_up`, in error for
    a person to retry it (published on mailmind.platform.bus)."""
    outbox_id: int
    account: str
    gave_up: bool


def _now():
    return int(time.time())


def enqueue(built, meta):
    """Persist a queued send. `built` = compose.build_message(...) output; `meta` =
    {account, idempotency_key, in_reply_to_pk, orig_message_id, subject, to, cc, bcc}. Returns
    (outbox_id, created). Kicks the worker."""
    orig = _original(meta.get("in_reply_to_pk"))         # its row may be gone by the time this goes out
    row = {
        "account": meta["account"],
        "idempotency_key": meta["idempotency_key"],
        "message_id": built["message_id"],
        "in_reply_to_pk": meta.get("in_reply_to_pk"),
        "orig_account": orig["account"] if orig else meta["account"] if meta.get("orig_message_id") else None,
        "orig_message_id": orig["message_id"] if orig else meta.get("orig_message_id"),
        "orig_thread_id": orig["thread_id"] if orig else None,
        "envelope": json.dumps(built["envelope"]),
        "recipients": json.dumps({"to": built["to_disp"], "cc": built["cc_disp"],
                                  "bcc": built["bcc_disp"]}),
        "subject": meta.get("subject") or "",
        "raw": built["raw"],
        "draft_id": meta.get("draft_id"),
        # grace window OR a future scheduled time (send-later) — QUEUE.due gates on this, so
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


def _original(pk, account=None, message_id=None):
    """The row of the message a send replies to: by Message-ID, since a pk may have been renumbered,
    else by the pk it was queued with. None once mail has moved out of every folder stored."""
    c = store.conn()
    try:
        return store.get_message_by_msgid(c, account, message_id) or (store.get_message(c, pk) if pk else None)
    finally:
        c.close()


def cancel(oid):
    """Undo Send — drop the row if it's still in its grace window. False if too late."""
    return _commit(lambda c: QUEUE.cancel_queued(c, oid))


def kick():
    QUEUE.kick()


def retry(oid):
    ok = _commit(lambda c: QUEUE.requeue(c, oid))
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
        state, error = r["state"], r["last_error"]
        if r["delivered"] and state != "sent":           # went out; only its Sent copy is owed
            state, error = ("error", SENT_COPY_FAILED) if state == "error" else ("sent", SENT_COPY_PENDING)
        scheduled = state == "queued" and na > _now() + SEND_DELAY + 5   # clearly future, not just the grace window
        out.append({
            "id": r["id"], "account": r["account"], "subject": r["subject"] or "(no subject)",
            "to": rcpt.get("to", []), "cc": rcpt.get("cc", []), "bcc_count": len(rcpt.get("bcc", [])),
            "state": state, "attempts": r["attempts"], "error": error,
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
    """Run fn(c) on a short-lived connection, commit, and return what fn returned."""
    c = store.conn()
    try:
        out = fn(c)
        c.commit()
        return out
    finally:
        c.close()


def _side_effects(oid, row, acct):
    """Done-once: Sent copy (non-Gmail) + \\Answered on the original, and the rest below.
    Each IMAP op is a job of its own on its account's IMAP gate (NOT one across the whole
    thing). A failure here never fails an already-delivered send. A Sent copy that failed is
    returned (scrubbed), for send_one to try again; any other step is logged and skipped."""
    unfiled = None
    if provider.resolve(acct).saves_sent and not row["sent_appended"]:
        _commit(lambda c: store.outbox_set(c, oid, sent_appended=1))   # Gmail auto-saves
    elif not row["sent_appended"]:
        # delivered before this run (a retry, or a crash's recovery): the last APPEND may have landed
        mid = row["message_id"] if row["delivered"] else None
        try:
            gates.run(acct["email"], lambda: smtpsend.append_to_sent(acct, row["raw"], message_id=mid),
                      priority=gates.Priority.AFTER_USER)
            _commit(lambda c: store.outbox_set(c, oid, sent_appended=1))
        except Exception as e:
            unfiled = f"sent copy failed: {type(e).__name__}"
            print(f"[outbox] Sent-copy failed for #{oid}: {smtpsend.sanitize_smtp_error(e)}", flush=True)
    replied_to = row["in_reply_to_pk"] or row["orig_message_id"]
    if replied_to and not row["answered_set"]:
        try:
            import mailops
            gates.run(row["orig_account"] or mailops.account_of(row["in_reply_to_pk"]) or gates.EVERY_ACCOUNT,
                      lambda: mailops.mark_answered(row["in_reply_to_pk"], row["orig_account"], row["orig_message_id"]),
                      priority=gates.Priority.AFTER_USER)
            _commit(lambda c: store.outbox_set(c, oid, answered_set=1))
        except Exception as e:
            print(f"[outbox] \\Answered failed for #{oid}: {type(e).__name__}", flush=True)
    # sent-no-reply: seed a follow-up on the replied-to thread (replies only — a brand-new compose
    # has no local thread_id until its Sent copy syncs). Best-effort, done-once, never fails the send.
    if replied_to and not row["followup_recorded"]:
        try:
            orig = _original(row["in_reply_to_pk"], row["orig_account"], row["orig_message_id"])
            tid = (orig["thread_id"] if orig else None) or row["orig_thread_id"]
            c = store.conn()
            if tid:
                store.followup_record_sent(c, row["account"], tid, orig["pk"] if orig else row["in_reply_to_pk"],
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
                def cleanup():           # the row goes with its copy: Edit re-checks it under the gate
                    removed = draft_copy_is_ours(dr) and smtpsend.delete_draft_uid(
                        accounts.get(dr["account"]), dr["folder"], dr["uid"], dr["uidvalidity"])

                    def forget(c):
                        store.draft_delete(c, row["draft_id"])
                        if removed:
                            store.delete_by_uids(c, dr["account"], dr["folder"], [dr["uid"]])
                    _commit(forget)
                    if removed:
                        bus.publish(DraftCopyRemoved(dr["account"], dr["folder"]))
                gates.run(dr["account"], cleanup, priority=gates.Priority.AFTER_USER)
            _commit(lambda c: store.outbox_set(c, oid, draft_deleted=1))
        except Exception as e:
            print(f"[outbox] draft cleanup failed for #{oid}: {type(e).__name__}", flush=True)
    return unfiled


def draft_copy_is_ours(d):
    """May this `drafts` row's copy be removed from the server? A removal has no Undo, so only one
    MailMind saved (the row holds its hash, and a save that never synced has no `messages` row to
    be flagged), one the server flags \\Draft, or one in a folder named Drafts. Received mail
    filed under "Contract drafts" stays, and a `drafts` row may well point at it: until now Edit
    was offered in any folder whose name held "draft"."""
    if d["raw_sha"] or folder_roles.names_drafts(d["folder"]):
        return True
    c = store.conn()
    try:
        return store.flagged_draft(c, d["account"], d["folder"], d["uid"])
    finally:
        c.close()


def send_one(oid):
    row = _commit(lambda c: QUEUE.claim(c, oid))
    if row is None:                          # lost the claim (cancelled / another worker)
        return
    try:
        acct = accounts.get(row["account"])  # host/port/creds ONLY from config (no SSRF)
    except KeyError:
        _commit(lambda c: QUEUE.give_up(c, row, "account removed"))
        bus.publish(Failed(oid, row["account"], True))
        return
    # Deliver exactly once. A recovered row (delivered=1, requeued by reset_stuck) skips
    # the wire send and only finishes bookkeeping — so a crash after send never re-sends.
    if not row["delivered"]:
        try:
            smtpsend.send(acct, row["raw"], json.loads(row["envelope"]))  # lock-free: SMTP != IMAP ops
        except Exception as e:
            err = smtpsend.sanitize_smtp_error(e)
            if smtpsend.permanent(e):          # a 5xx: the same answer again, so no retry
                _commit(lambda c: QUEUE.give_up(c, row, err))
                terminal, delay = True, 0
            else:
                terminal, delay = _commit(lambda c: QUEUE.retry_later(c, row, err, _now()))
            print(f"[outbox] send #{oid} failed ({err}); "
                  f"{'giving up' if terminal else f'retry in {delay}s'}", flush=True)
            bus.publish(Failed(oid, row["account"], terminal))
            return
        _commit(lambda c: QUEUE.mark_gate(c, oid))  # delivered=1, committed BEFORE side effects
    # delivered — side effects, then mark sent (all guarded so a DB hiccup can't strand a
    # delivered row; next pass sees delivered=1 and only retries bookkeeping). A Sent copy that
    # failed sends the row back to the queue instead, delivered, until it's filed or gives up.
    try:
        unfiled = _side_effects(oid, row, acct)
        if unfiled:
            # the copy's tries count from the delivery, not on from the send's
            tried = {"id": oid, "attempts": row["attempts"] if row["delivered"] else 0}
            terminal, delay = _commit(lambda c: QUEUE.retry_later(c, tried, unfiled, _now()))
            print(f"[outbox] sent #{oid}; its Sent copy "
                  f"{'gives up' if terminal else f'is tried again in {delay}s'}", flush=True)
        else:
            _commit(lambda c: QUEUE.finish(c, oid, datetime.now(timezone.utc).isoformat()))
            print(f"[outbox] sent #{oid}", flush=True)
        bus.publish(Sent(oid, row["account"]))
    except Exception as e:
        print(f"[outbox] post-send bookkeeping for #{oid} deferred: {type(e).__name__}", flush=True)


def drain():
    if not profile.writes_allowed():     # read-only: sends wait here for a full run (D4)
        return
    QUEUE.drain(send_one, _now())        # one bad row is logged and never aborts the rest


def worker_loop():
    QUEUE.run_forever(drain)             # recovers rows a crash left mid-send, then drains
