"""Compose's tables: `outbox` and `drafts`. Each function takes its caller's connection, from connect(),
and leaves the commit to it."""
import sqlite3

from mailmind.platform import settings
from mailmind.platform.db import database


def connect():
    return database.connect(settings.current().db)


def outbox_insert(c, row):
    """Insert a queued send. Returns (id, created): created=False if the
    idempotency_key already exists (a double-submit), so the caller never enqueues
    the same logical send twice. Race-safe: a concurrent identical submit that loses
    the UNIQUE(idempotency_key) constraint falls back to the existing row."""
    existing = c.execute("SELECT id FROM outbox WHERE idempotency_key=?",
                         (row["idempotency_key"],)).fetchone()
    if existing:
        return existing["id"], False
    try:
        cur = c.execute(
            """INSERT INTO outbox(account,idempotency_key,message_id,in_reply_to_pk,
                 orig_account,orig_message_id,orig_thread_id,
                 envelope,recipients,subject,raw,state,next_attempt_at,draft_id,created_at)
               VALUES(:account,:idempotency_key,:message_id,:in_reply_to_pk,
                 :orig_account,:orig_message_id,:orig_thread_id,
                 :envelope,:recipients,:subject,:raw,'queued',:next_attempt_at,:draft_id,:created_at)
               RETURNING id""", {"orig_account": None, "orig_message_id": None, "orig_thread_id": None, **row})
        return cur.fetchone()["id"], True
    except sqlite3.IntegrityError:   # someone inserted the same key between our SELECT and INSERT
        r = c.execute("SELECT id FROM outbox WHERE idempotency_key=?",
                      (row["idempotency_key"],)).fetchone()
        return (r["id"] if r else None), False


def outbox_set(c, oid, **flags):
    cols = {k: int(v) for k, v in flags.items() if k in ("wire_begun", "sent_appended", "answered_set", "draft_deleted", "followup_recorded")}
    if not cols:
        return
    sets = ", ".join(f"{k}=?" for k in cols)
    c.execute(f"UPDATE outbox SET {sets} WHERE id=?", [*cols.values(), oid])


def outbox_send_now(c, oid, now_ts):
    """Bring a scheduled (queued, future next_attempt_at) send forward to now."""
    cur = c.execute("UPDATE outbox SET next_attempt_at=? WHERE id=? AND state='queued'",
                    (int(now_ts), oid))
    return cur.rowcount == 1


def outbox_holds_draft(c, did):
    """Is a send of this draft queued or going out now? Its copy stays in Drafts until delivery."""
    return c.execute("SELECT 1 FROM outbox WHERE draft_id=? AND state IN ('queued','sending') LIMIT 1",
                     (did,)).fetchone() is not None


def outbox_list(c, limit=50):
    return c.execute(
        """SELECT id,account,subject,recipients,state,attempts,last_error,created_at,sent_at,next_attempt_at,
                  delivered,wire_begun
           FROM outbox ORDER BY id DESC LIMIT ?""", (limit,)).fetchall()


# ------------------------------------------------------------------- drafts
def draft_get(c, did):
    return c.execute("SELECT * FROM drafts WHERE id=?", (did,)).fetchone()


def draft_by_key(c, key):
    """The draft a composer's first save made, by the composer's key; the newest if there are two."""
    return c.execute("SELECT * FROM drafts WHERE idempotency_key=? ORDER BY id DESC LIMIT 1", (key,)).fetchone()


def draft_set_key(c, did, key):
    c.execute("UPDATE drafts SET idempotency_key=? WHERE id=?", (key, did))


def draft_by_msgid(c, account, message_id):
    if not message_id:
        return None
    return c.execute("SELECT * FROM drafts WHERE account=? AND message_id=?",
                     (account, message_id)).fetchone()


def draft_upsert(c, row):
    """Insert (row has no id) or update (row has id) a draft tracking record; returns id. A row
    that has gone (the draft was sent or discarded elsewhere) is inserted afresh under a new id,
    so the copy just saved is tracked rather than left behind by every later save."""
    if row.get("id"):
        cur = c.execute("""UPDATE drafts SET account=:account, folder=:folder, uid=:uid, message_id=:message_id,
                       uidvalidity=:uidvalidity, in_reply_to_pk=:in_reply_to_pk, mode=:mode,
                       subject=:subject, updated_at=:updated_at WHERE id=:id""", row)
        if cur.rowcount:
            return row["id"]
    cur = c.execute("""INSERT INTO drafts(account,folder,uid,message_id,uidvalidity,
                         in_reply_to_pk,mode,subject,created_at,updated_at)
                       VALUES(:account,:folder,:uid,:message_id,:uidvalidity,:in_reply_to_pk,
                         :mode,:subject,:created_at,:updated_at) RETURNING id""", row)
    return cur.fetchone()["id"]


def draft_set_resume(c, did, own_text, tail_text, tail_html, bcc, raw_sha):
    """Record what the composer held at this save (see _m20_draft_resume)."""
    c.execute("UPDATE drafts SET own_text=?, tail_text=?, tail_html=?, bcc=?, raw_sha=? WHERE id=?",
              (own_text, tail_text, tail_html, bcc, raw_sha, did))


def flagged_draft(c, account, folder, uid):
    """Does the server flag this message \\Draft (as last synced)? A folder can hold received mail
    whatever it is called, and that must never be removed as a replaced draft."""
    r = c.execute("SELECT draft FROM messages WHERE account=? AND folder=? AND uid=?", (account, folder, uid)).fetchone()
    return bool(r and r["draft"])


def draft_repoint(c, seen, folder, uid, uidvalidity, forget=False):
    """Track the copy that was opened rather than the one last saved: another app re-saved the
    draft under the same Message-ID, so the next save or send must replace this one. seen: the
    drafts row as read; a save that has moved it on since wins, and nothing changes. forget: the
    copy isn't byte-for-byte what MailMind saved, so the recorded text and Bcc no longer describe
    it. The recorded quote stays; it is only ever trusted where the text still ends with it.
    Returns whether the row was re-pointed."""
    cur = c.execute("UPDATE drafts SET folder=?, uid=?, uidvalidity=? WHERE id=? AND uid IS ? AND message_id IS ?",
                    (folder, uid, uidvalidity, seen["id"], seen["uid"], seen["message_id"]))
    if cur.rowcount and forget:
        c.execute("UPDATE drafts SET own_text=NULL, bcc=NULL, raw_sha=NULL WHERE id=?", (seen["id"],))
    return bool(cur.rowcount)


def draft_delete(c, did):
    c.execute("DELETE FROM drafts WHERE id=?", (did,))
