"""Compose's tables: `outbox` and `drafts`. Each function takes its caller's connection and leaves the commit to it."""
from mailmind.platform.db import driver


def rekey_thread(c, account, survivor, absorbed):
    """Thread `absorbed` merged into `survivor`: a queued reply's follow-up lands on the survivor, in the merge's
    transaction."""
    c.execute("UPDATE outbox SET orig_thread_id=? WHERE orig_account=? AND orig_thread_id=?", (survivor, account, absorbed))


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
            """INSERT INTO outbox(account,idempotency_key,message_id,
                 orig_account,orig_message_id,orig_thread_id,original_key,
                 envelope,recipients,subject,raw,state,next_attempt_at,wait_kind,draft_id,created_at)
               VALUES(:account,:idempotency_key,:message_id,
                 :orig_account,:orig_message_id,:orig_thread_id,:original_key,
                 :envelope,:recipients,:subject,:raw,'queued',:next_attempt_at,:wait_kind,:draft_id,:created_at)
               RETURNING id""", {"orig_account": None, "orig_message_id": None, "orig_thread_id": None, "original_key": None,
                                  "wait_kind": "grace", **row})
        return cur.fetchone()["id"], True
    except driver.IntegrityError:   # someone inserted the same key between our SELECT and INSERT
        r = c.execute("SELECT id FROM outbox WHERE idempotency_key=?",
                      (row["idempotency_key"],)).fetchone()
        return (r["id"] if r else None), False


def outbox_set(c, oid, **flags):
    cols = {k: int(v) for k, v in flags.items() if k in ("wire_begun", "sent_appended", "answered_set", "draft_deleted", "followup_recorded")}
    if "wait_kind" in flags:
        cols["wait_kind"] = flags["wait_kind"]
    if not cols:
        return
    sets = ", ".join(f"{k}=?" for k in cols)
    c.execute(f"UPDATE outbox SET {sets} WHERE id=?", [*cols.values(), oid])


def outbox_send_now(c, oid, now_ts):
    """Bring a queued send with a time to wait forward to now."""
    cur = c.execute("UPDATE outbox SET next_attempt_at=?, wait_kind='grace' WHERE id=? AND state='queued'",
                    (int(now_ts), oid))
    return cur.rowcount == 1


def outbox_holds_draft(c, did):
    """Is a send of this draft queued or going out now? Its copy stays in Drafts until delivery."""
    return c.execute("SELECT 1 FROM outbox WHERE draft_id=? AND state IN ('queued','sending') LIMIT 1",
                     (did,)).fetchone() is not None


def outbox_list(c, limit=50):
    return c.execute(
        """SELECT id,account,subject,recipients,state,attempts,last_error,created_at,sent_at,next_attempt_at,
                  delivered,wire_begun,sent_appended,wait_kind
           FROM outbox ORDER BY id DESC LIMIT ?""", (limit,)).fetchall()


def outbox_get(c, oid):
    return c.execute("SELECT * FROM outbox WHERE id=?", (oid,)).fetchone()


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
                       uidvalidity=:uidvalidity, mode=:mode,
                       subject=:subject, updated_at=:updated_at WHERE id=:id""", row)
        if cur.rowcount:
            return row["id"]
    cur = c.execute("""INSERT INTO drafts(account,folder,uid,message_id,uidvalidity,
                         mode,subject,created_at,updated_at)
                       VALUES(:account,:folder,:uid,:message_id,:uidvalidity,
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


def draft_note_save(c, did, mode, subject, version, saved_by, updated_at):
    """A save's facts about the composition. What says where the copy is waits for the push."""
    c.execute("UPDATE drafts SET mode=?, subject=?, version=?, saved_by=?, updated_at=? WHERE id=?",
              (mode, subject, version, saved_by, updated_at, did))


def draft_note_discard(c, did, version, updated_at):
    """A discard's mark on the row, which says nothing new about the composition."""
    c.execute("UPDATE drafts SET version=?, updated_at=? WHERE id=?", (version, updated_at, did))


def draft_settle_push(c, did, account, folder, uid, message_id, uidvalidity, pushed_at):
    c.execute("""UPDATE drafts SET account=?, folder=?, uid=?, message_id=?, uidvalidity=?, pushed_at=? WHERE id=?""",
              (account, folder, uid, message_id, uidvalidity, pushed_at, did))


def draft_quote(c, did):
    """The quote the draft's newest save holds: the journal's, or its copy's."""
    r = c.execute("SELECT tail_text, tail_html FROM draft_journal WHERE draft_id=? AND raw IS NOT NULL", (did,)).fetchone()
    return r or c.execute("SELECT tail_text, tail_html FROM drafts WHERE id=?", (did,)).fetchone()


# ------------------------------------------------------------------- the journal: what a draft still asks of its server
def journal_get(c, did):
    return c.execute("SELECT * FROM draft_journal WHERE draft_id=?", (did,)).fetchone()


def journal_put(c, did, version, account, message_id, raw, raw_sha, own_text, tail_text, tail_html, bcc, saved_at, due_at, attempts):
    """This save replaces the one before it. `tried` stays: the copy a try may have left goes when this one lands."""
    c.execute("""INSERT INTO draft_journal(draft_id,version,account,discard,message_id,raw,raw_sha,own_text,tail_text,tail_html,bcc,
                                           saved_at,due_at,attempts)
                 VALUES(?,?,?,0,?,?,?,?,?,?,?,?,?,?)
                 ON CONFLICT(draft_id) DO UPDATE SET version=excluded.version, account=excluded.account, discard=0,
                   message_id=excluded.message_id, raw=excluded.raw, raw_sha=excluded.raw_sha, own_text=excluded.own_text,
                   tail_text=excluded.tail_text, tail_html=excluded.tail_html, bcc=excluded.bcc, saved_at=excluded.saved_at,
                   due_at=excluded.due_at, attempts=excluded.attempts""",
              (did, version, account, message_id, raw, raw_sha, own_text, tail_text, tail_html, bcc, saved_at, due_at, attempts))


def journal_discard(c, did, version, account, now):
    """All that's left to ask is that the copy goes, in place of any save still waiting."""
    c.execute("""INSERT INTO draft_journal(draft_id,version,account,discard,saved_at,due_at)
                 VALUES(?,?,?,1,?,?)
                 ON CONFLICT(draft_id) DO UPDATE SET version=excluded.version, discard=1, message_id=NULL, raw=NULL, raw_sha=NULL,
                   own_text=NULL, tail_text=NULL, tail_html=NULL, bcc=NULL, saved_at=excluded.saved_at,
                   due_at=excluded.due_at, attempts=0""",
              (did, version, account, now, now))


def journal_tried(c, did, version, message_id):
    """A try may reach the server from here on. False if the row or this version has gone meanwhile."""
    return c.execute("UPDATE draft_journal SET tried=? WHERE draft_id=? AND version=? AND discard=0",
                     (message_id, did, version)).rowcount == 1


def journal_settled(c, did, version):
    """The push of `version` landed. A save made since then stays to go, with no copy left to wonder about."""
    if not c.execute("DELETE FROM draft_journal WHERE draft_id=? AND version=?", (did, version)).rowcount:
        c.execute("UPDATE draft_journal SET tried=NULL, attempts=0 WHERE draft_id=?", (did,))


def journal_failed(c, did, attempts, due_at):
    c.execute("UPDATE draft_journal SET attempts=?, due_at=? WHERE draft_id=?", (attempts, due_at, did))


# A send about to go holds its draft: the outbox has the bytes, and the copy is removed on delivery. One scheduled for
# later leaves the copy to catch up in the meantime.
_NOT_ABOUT_TO_GO = """NOT EXISTS (SELECT 1 FROM outbox o WHERE o.draft_id=j.draft_id
                                  AND o.state IN ('queued','sending') AND o.next_attempt_at <= ?)"""


def journal_due(c, now, soon):
    return c.execute(f"SELECT draft_id, discard FROM draft_journal j WHERE due_at<=? AND {_NOT_ABOUT_TO_GO} ORDER BY due_at",
                     (now, soon)).fetchall()


def journal_next_due(c, after, soon):
    return c.execute(f"SELECT MIN(due_at) AS at FROM draft_journal j WHERE due_at>? AND {_NOT_ABOUT_TO_GO}",
                     (after, soon)).fetchone()["at"]


def journal_overdue(c, before):
    """What should have reached its server by `before` and hasn't, for the tray. Any send of it is in the tray already."""
    return c.execute("""SELECT j.draft_id, j.account, j.discard, j.saved_at, j.attempts, d.subject
                        FROM draft_journal j JOIN drafts d ON d.id=j.draft_id
                        WHERE (j.attempts>0 OR j.due_at<=?) AND NOT EXISTS (SELECT 1 FROM outbox o WHERE o.draft_id=j.draft_id
                                                                            AND o.state IN ('queued','sending'))
                        ORDER BY j.saved_at DESC""", (before,)).fetchall()


def journal_move(c, did, to_did):
    """A save goes on as a draft of its own, leaving the other's copy be."""
    c.execute("UPDATE draft_journal SET draft_id=?, tried=NULL WHERE draft_id=?", (to_did, did))
