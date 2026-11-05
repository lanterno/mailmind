"""SQLite store for MailMind: messages + AI summaries + per-account sync state."""
import json
import sqlite3
from datetime import datetime, timezone, timedelta

from mailmind.mailbox.domain import identity
from mailmind.platform import settings
from mailmind.platform.db import database, migrate


def conn():
    return database.connect(settings.current().db)


def init():
    """Bring the data directory's database to the newest schema (see mailmind.platform.db)."""
    migrate.init(settings.current().db)
    c = conn()
    try:
        repair_keys(c)
        c.commit()
    finally:
        c.close()


# ---- unsubscribe / sender-cleanup helpers ----
def set_list_unsub(c, pk, raw, method):
    c.execute("UPDATE messages SET list_unsub_raw=?, list_unsub_method=? WHERE pk=?", (raw, method, pk))


def unsub_log_get(c, key):
    return c.execute("SELECT * FROM unsub_log WHERE idempotency_key=?", (key,)).fetchone()


def unsub_log_put(c, from_addr, method, status, http_status, ts, key, accounts):
    """Idempotent insert; returns the row (existing on dupe key, so a repeat never re-fires).
    `accounts` are those the sender wrote to."""
    c.execute("""INSERT OR IGNORE INTO unsub_log(from_addr,method,status,http_status,ts,idempotency_key,accounts)
                 VALUES(?,?,?,?,?,?,?)""", (from_addr, method, status, http_status, ts, key, json.dumps(accounts)))
    return c.execute("SELECT * FROM unsub_log WHERE idempotency_key=?", (key,)).fetchone()


# ---- offline read-mutation queue: the rows; readmutq.QUEUE moves them through their states ----
def queue_flags(c, row, flags, mutation_id, when):
    """Queue a flag change on message `row` for the server and show it on the row at once, in the
    caller's transaction. The queue's row keeps the flags it replaced, which a change the server
    refuses, or one the queue gives up on, puts back. False if that id was queued already."""
    acct, mid = row["account"], (row["message_id"] or "").strip()
    payload = {"pk": row["pk"], "uid": row["uid"], "flags": flags,
               "was": {k: bool(row[k]) for k in flags}}
    _id, created = read_mut_insert(c, {"account": acct, "idempotency_key": f"flag:{acct}:{mid or row['pk']}:{mutation_id}",
                                       "op": "flag", "message_id": mid, "src_folder": row["folder"],
                                       "dest": None, "payload": json.dumps(payload),
                                       "next_attempt_at": 0, "created_at": when})
    if created:
        set_local_flags(c, row["pk"], **{k: int(bool(v)) for k, v in flags.items()})
    return created


def read_mut_insert(c, row):
    """Insert a queued mutation; (id, created) — created=False on idempotency_key dupe. Race-safe."""
    existing = c.execute("SELECT id FROM read_mutations WHERE idempotency_key=?",
                         (row["idempotency_key"],)).fetchone()
    if existing:
        return existing["id"], False
    try:
        cur = c.execute(
            """INSERT INTO read_mutations(account,idempotency_key,op,message_id,src_folder,dest,
                 payload,state,next_attempt_at,created_at)
               VALUES(:account,:idempotency_key,:op,:message_id,:src_folder,:dest,
                 :payload,'queued',:next_attempt_at,:created_at) RETURNING id""", row)
        return cur.fetchone()["id"], True
    except sqlite3.IntegrityError:
        r = c.execute("SELECT id FROM read_mutations WHERE idempotency_key=?",
                      (row["idempotency_key"],)).fetchone()
        return (r["id"] if r else None), False


def read_mut_get(c, mid):
    return c.execute("SELECT * FROM read_mutations WHERE id=?", (mid,)).fetchone()


def read_mut_set_dest(c, mid, dest):
    """Persist the destination the worker resolved online, so a later undo can find the message."""
    c.execute("UPDATE read_mutations SET dest=? WHERE id=?", (dest, mid))


def read_mut_set_payload(c, mid, payload):
    c.execute("UPDATE read_mutations SET payload=? WHERE id=?", (payload, mid))


def read_mut_open(c, limit):
    """The changes that haven't reached a server, newest first: waiting, being tried or given up."""
    return c.execute("SELECT * FROM read_mutations WHERE applied=0 AND state IN ('queued', 'running', 'error') "
                     "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def read_mut_open_counts(c):
    """{state: how many} of the changes that haven't reached a server."""
    return {r["state"]: r["n"] for r in c.execute(
        "SELECT state, COUNT(*) n FROM read_mutations WHERE applied=0 "
        "AND state IN ('queued', 'running', 'error') GROUP BY state")}


def read_mut_latest_by_msgid(c, account, message_id):
    """The most recent read-mutation row for a message (any state) — undo uses it to decide
    cancel-vs-counter-move and to recover the resolved destination folder."""
    return c.execute("SELECT * FROM read_mutations WHERE account=? AND message_id=? "
                     "ORDER BY id DESC LIMIT 1", (account, message_id)).fetchone()


def record_sync_result(c, account, ok, err_name=None, kind=None, when=None):
    """Record an account's INBOX sync outcome on its sync_state row — health columns ONLY (never
    touches last_uid/uidvalidity). Stores at most the exception CLASS name + a coarse kind enum;
    NEVER a raw exception message (preserves the error-scrubbing invariant)."""
    when = when or datetime.now(timezone.utc).isoformat()
    c.execute("INSERT OR IGNORE INTO sync_state(account, folder) VALUES(?, 'INBOX')", (account,))
    if ok:
        c.execute("UPDATE sync_state SET last_ok_ts=?, sync_count=COALESCE(sync_count,0)+1, "
                  "last_error=NULL, last_error_ts=NULL, last_error_kind=NULL "
                  "WHERE account=? AND folder='INBOX'", (when, account))
    else:
        c.execute("UPDATE sync_state SET last_error=?, last_error_ts=?, last_error_kind=?, "
                  "sync_count=COALESCE(sync_count,0)+1 WHERE account=? AND folder='INBOX'",
                  (str(err_name or "Error")[:40], when, (kind or "UNKNOWN"), account))


def account_health(c):
    """Per (account, folder) health rows for the health surface. Pure read."""
    return c.execute(
        "SELECT account, folder, last_sync, last_ok_ts, last_error, last_error_ts, last_error_kind, "
        "COALESCE(sync_count,0) AS sync_count FROM sync_state ORDER BY account, folder").fetchall()


def clear_sync_error(c, account):
    c.execute("UPDATE sync_state SET last_error=NULL, last_error_ts=NULL, last_error_kind=NULL "
              "WHERE account=? AND folder='INBOX'", (account,))


def get_message(c, pk):
    return c.execute("SELECT * FROM messages WHERE pk=?", (pk,)).fetchone()


def get_message_by_msgid(c, account, message_id):
    """Latest local row for a message by (account, RFC822 Message-ID) — the durable key the
    read-mutation worker uses to re-resolve a message whose pk/uid may have changed via reconcile."""
    if not message_id:
        return None
    return c.execute("SELECT * FROM messages WHERE account=? AND message_id=? ORDER BY pk DESC LIMIT 1",
                     (account, message_id)).fetchone()


def delete_message(c, pk):
    row = c.execute("SELECT message_key FROM messages WHERE pk=?", (pk,)).fetchone()
    fts_delete(c, pk)                       # drop the FTS row first (single delete chokepoint)
    c.execute("DELETE FROM summaries WHERE message_pk=?", (pk,))
    c.execute("DELETE FROM messages WHERE pk=?", (pk,))
    if row and row["message_key"] is not None:
        c.execute("UPDATE message_identity SET gone_at=? WHERE key=? "
                  "AND NOT EXISTS (SELECT 1 FROM messages WHERE message_key=?)",
                  (int(datetime.now(timezone.utc).timestamp()), row["message_key"], row["message_key"]))


def mint_key(c, account, message_id, from_addr, date_iso, subject):
    """The message's key, minted the first time any copy of it is seen."""
    who = (account, identity.of(message_id, from_addr, date_iso, subject))
    c.execute("INSERT OR IGNORE INTO message_identity(account, identity) VALUES(?,?)", who)
    return c.execute("SELECT key FROM message_identity WHERE account=? AND identity=?", who).fetchone()[0]


def repair_keys(c):
    """Give a key to each row that has none: only a run of an older commit writes one."""
    rows = c.execute("SELECT pk, account, message_id, from_addr, date_iso, subject FROM messages "
                     "WHERE message_key IS NULL").fetchall()
    for r in rows:
        key = mint_key(c, r["account"], r["message_id"], r["from_addr"], r["date_iso"], r["subject"])
        c.execute("UPDATE messages SET message_key=? WHERE pk=?", (key, r["pk"]))
        c.execute("""INSERT OR IGNORE INTO assessments(message_key, summary, importance, category, action, model,
                                                       created_at)
                     SELECT ?, summary, importance, category, action, model, created_at
                     FROM summaries WHERE message_pk=?""", (key, r["pk"]))
        c.execute("""INSERT OR IGNORE INTO snoozes(message_key, folder, until)
                     SELECT message_key, folder, snoozed_until FROM messages
                     WHERE pk=? AND snoozed_until IS NOT NULL""", (r["pk"],))


def forget_gone_messages(c, now_ts, days=30):
    """A message with no copy left for `days` goes, and what the AI said of it. Until then a copy
    that comes back (a move, an Undo, a resync) finds its key and its assessment."""
    return c.execute("DELETE FROM message_identity WHERE gone_at < ?", (now_ts - days * 86400,)).rowcount


def mark_pending_removal(c, pk, mutation_id):
    """Leave message `pk` out of every list while the queued move `mutation_id` (a read_mutations
    id) takes it away. The row stays, so a move that doesn't happen can list it again."""
    c.execute("UPDATE messages SET pending_removal=? WHERE pk=?", (mutation_id, pk))


def restore_pending(c, mutation_id):
    """The queued move `mutation_id` won't happen: list its message again. Returns how many rows
    it brought back."""
    return c.execute("UPDATE messages SET pending_removal=NULL WHERE pending_removal=?",
                     (mutation_id,)).rowcount


def pending_removal_pks(c, mutation_id):
    """The rows the queued move `mutation_id` is taking away."""
    return [r["pk"] for r in c.execute("SELECT pk FROM messages WHERE pending_removal=?", (mutation_id,))]


def get_watermark(c, account, folder="INBOX"):
    r = c.execute("SELECT last_uid FROM sync_state WHERE account=? AND folder=?",
                  (account, folder)).fetchone()
    return (r["last_uid"] or 0) if r else 0


def set_watermark(c, account, last_uid, last_sync, folder="INBOX"):
    c.execute(
        """INSERT INTO sync_state(account,folder,last_sync,last_uid) VALUES(?,?,?,?)
           ON CONFLICT(account,folder) DO UPDATE SET last_sync=excluded.last_sync,
             last_uid=MAX(sync_state.last_uid, excluded.last_uid)""",
        (account, folder, last_sync, last_uid),
    )


def get_uidvalidity(c, account, folder="INBOX"):
    r = c.execute("SELECT uidvalidity FROM sync_state WHERE account=? AND folder=?",
                  (account, folder)).fetchone()
    return r["uidvalidity"] if r else None


def get_modseq(c, account, folder):
    r = c.execute("SELECT highest_modseq FROM sync_state WHERE account=? AND folder=?",
                  (account, folder)).fetchone()
    return r["highest_modseq"] if r else None


def set_modseq(c, account, folder, modseq):
    c.execute("UPDATE sync_state SET highest_modseq=? WHERE account=? AND folder=?", (modseq, account, folder))


def set_uidvalidity(c, account, folder, uidvalidity):
    c.execute(
        """INSERT INTO sync_state(account,folder,uidvalidity) VALUES(?,?,?)
           ON CONFLICT(account,folder) DO UPDATE SET uidvalidity=excluded.uidvalidity""",
        (account, folder, uidvalidity),
    )


def undo_push(c, entry, group, keep):
    """Put `entry` on top of the undo stack, or, when the top gathers `group`, add it to that
    group's children. Then keep only the newest `keep` entries."""
    top = c.execute("SELECT id, grp, entry FROM undo_entries ORDER BY id DESC LIMIT 1").fetchone()
    if group and top is not None and top["grp"] == group:
        held = json.loads(top["entry"])
        held["children"].append(entry)
        c.execute("UPDATE undo_entries SET entry=? WHERE id=?", (json.dumps(held), top["id"]))
    elif group:
        c.execute("INSERT INTO undo_entries(grp, entry, created_at) VALUES(?,?,?)",
                  (group, json.dumps({"kind": "bulk", "group": group, "label": entry.get("label", "Updated"),
                                      "children": [entry]}), datetime.now(timezone.utc).isoformat()))
    else:
        c.execute("INSERT INTO undo_entries(grp, entry, created_at) VALUES(NULL,?,?)",
                  (json.dumps(entry), datetime.now(timezone.utc).isoformat()))
    c.execute("DELETE FROM undo_entries WHERE id NOT IN (SELECT id FROM undo_entries ORDER BY id DESC LIMIT ?)",
              (keep,))


def undo_put_back(c, entry):
    """Put an entry taken off the stack back on top: it, or what's left of its group, can be tried
    again."""
    c.execute("INSERT INTO undo_entries(grp, entry, created_at) VALUES(?,?,?)",
              (entry.get("group") if entry.get("kind") == "bulk" else None, json.dumps(entry),
               datetime.now(timezone.utc).isoformat()))


def undo_pop(c):
    """Take the newest entry off the undo stack: the entry, or None when the stack is empty."""
    top = c.execute("SELECT id, entry FROM undo_entries ORDER BY id DESC LIMIT 1").fetchone()
    if top is None:
        return None
    c.execute("DELETE FROM undo_entries WHERE id=?", (top["id"],))
    return json.loads(top["entry"])


def undo_forget_move(c, account, message_id, src_folder):
    """A queued move was dropped, so there is nothing to reverse: take the newest entry for it off
    the stack, or out of its group."""
    def names(e):
        return (e.get("kind") in ("move", "gmail_archive") and e.get("account") == account
                and e.get("message_id") == message_id and e.get("src_folder") == src_folder)
    for r in c.execute("SELECT id, entry FROM undo_entries ORDER BY id DESC").fetchall():
        held = json.loads(r["entry"])
        if held.get("kind") != "bulk":
            if names(held):
                c.execute("DELETE FROM undo_entries WHERE id=?", (r["id"],))
                return
            continue
        children = held["children"]
        hit = next((i for i in range(len(children) - 1, -1, -1) if names(children[i])), None)
        if hit is None:
            continue
        del children[hit]
        if children:
            c.execute("UPDATE undo_entries SET entry=? WHERE id=?", (json.dumps(held), r["id"]))
        else:
            c.execute("DELETE FROM undo_entries WHERE id=?", (r["id"],))
        return


def undo_entries(c):
    """The undo stack, oldest first."""
    return [json.loads(r["entry"]) for r in c.execute("SELECT entry FROM undo_entries ORDER BY id")]


def get_scan_state(c, account, folder):
    """How much of the folder a scan has read: 'never', 'recent' (its newest messages only) or
    'synced' (every message up to the watermark)."""
    r = c.execute("SELECT scan_state FROM sync_state WHERE account=? AND folder=?",
                  (account, folder)).fetchone()
    return r["scan_state"] if r else "never"


def set_scan_state(c, account, folder, state):
    c.execute(
        """INSERT INTO sync_state(account,folder,scan_state) VALUES(?,?,?)
           ON CONFLICT(account,folder) DO UPDATE SET scan_state=excluded.scan_state""",
        (account, folder, state),
    )


def reset_folder(c, account, folder):
    """UIDVALIDITY changed (or forced refresh): drop this folder's rows + watermark
    so it re-syncs from scratch. Summaries cascade with the messages."""
    pks = [r["pk"] for r in c.execute(
        "SELECT pk FROM messages WHERE account=? AND folder=?", (account, folder))]
    for pk in pks:
        delete_message(c, pk)
    c.execute("UPDATE sync_state SET last_uid=0, scan_state='never', highest_modseq=NULL "
              "WHERE account=? AND folder=?", (account, folder))
    return len(pks)


def store_body(c, pk, raw, when):
    c.execute("INSERT OR REPLACE INTO bodies(message_pk,raw,fetched_at) VALUES(?,?,?)",
              (pk, raw, when))
    c.execute("UPDATE messages SET body_fetched=1 WHERE pk=?", (pk,))


def get_body(c, pk):
    r = c.execute("SELECT raw FROM bodies WHERE message_pk=?", (pk,)).fetchone()
    return r["raw"] if r else None


# ------------------------------------------------------------------------- FTS
# Standalone FTS5 over (subject, from_name, from_addr, snippet, body_text); rowid=pk.
# Standalone (not external-content) so deletes are a plain DELETE WHERE rowid — no
# old-values-required footgun. Helpers are defensive: a missing index never breaks ingest.
import re as _re

_TAG = _re.compile(r"<[^>]+>")
# Drop the *contents* of non-text elements before tag-stripping, else CSS rules,
# <script> JSON-LD and <head> metadata leak into the "plain text" (and snippets).
_NOTEXT = _re.compile(r"(?is)<(script|style|head|title)\b[^>]*>.*?</\1>")
_COMMENT = _re.compile(r"(?s)<!--.*?-->")
# Invisible "preheader padding" newsletters stuff after the hook line (zero-width spaces,
# combining grapheme joiner, soft hyphen, BOM, bidi marks) — strip for a clean preview.
_INVIS = _re.compile("[​‌‍⁠﻿͏­‎‏]")
_FTS_CAP = 100_000  # bound a single body's indexed text


def _snippet_of(body_text):
    """A clean one-paragraph preview for the list/river: decode stray entities, drop invisible
    padding (incl. &zwnj;/&nbsp; spacers once decoded), collapse whitespace."""
    import html as _html
    return " ".join(_INVIS.sub("", _html.unescape(body_text or "")).split())[:280]


def _strip_jsonld(t):
    """Some senders embed a JSON-LD block (schema.org structured data) in their text part.
    Remove each balanced [..]/{..} block carrying an @context, so previews read as prose."""
    out = t or ""
    guard = 0
    while "@context" in out and guard < 12:
        guard += 1
        idx = out.find("@context")
        start = max(out.rfind("[", 0, idx), out.rfind("{", 0, idx))
        if start < 0:
            break
        j = start - 1                      # extend back over a wrapping opener (e.g. the [ before {)
        while j >= 0 and out[j] in "[{ \t\r\n":
            if out[j] in "[{":
                start = j
            j -= 1
        depth = 0
        end = -1
        for i in range(start, len(out)):
            if out[i] in "[{":
                depth += 1
            elif out[i] in "]}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end < 0:
            break
        out = out[:start] + " " + out[end + 1:]
    return out


def _body_text_of(parsed):
    """Plain-text body for indexing: prefer text/plain, else strip tags from html."""
    t = parsed.get("text")
    if not t and parsed.get("html"):
        import html as _html
        h = _COMMENT.sub(" ", parsed["html"])
        h = _NOTEXT.sub(" ", h)
        t = _html.unescape(_TAG.sub(" ", h))
    return _strip_jsonld(t or "")[:_FTS_CAP]


def _recipients_of(parsed):
    """To + Cc as one searchable string (names + addresses), for the to:/cc: operators."""
    return ((parsed.get("to") or "") + " " + (parsed.get("cc") or "")).strip()[:2000]


def fts_index(c, pk):
    """(Re)index one message from its current columns. Idempotent (delete+insert)."""
    try:
        r = c.execute("SELECT subject,from_name,from_addr,recipients,snippet,body_text "
                      "FROM messages WHERE pk=?", (pk,)).fetchone()
        if not r:
            return
        c.execute("DELETE FROM msg_fts WHERE rowid=?", (pk,))
        c.execute("INSERT INTO msg_fts(rowid,subject,from_name,from_addr,recipients,snippet,body_text) "
                  "VALUES(?,?,?,?,?,?,?)",
                  (pk, r["subject"] or "", r["from_name"] or "", r["from_addr"] or "",
                   r["recipients"] or "", r["snippet"] or "", r["body_text"] or ""))
    except sqlite3.OperationalError:
        pass  # index not present yet (pre-migration) — safe to skip


def fts_set_body(c, pk, body_text):
    """Fill body_text once a body is fetched, derive a list/river snippet if none was
    captured at ingest, and refresh the FTS row."""
    bt = body_text or ""
    snip = _snippet_of(bt)
    c.execute("UPDATE messages SET body_text=?, "
              "snippet=CASE WHEN snippet IS NULL OR snippet='' THEN ? ELSE snippet END "
              "WHERE pk=?", (bt, snip, pk))
    fts_index(c, pk)


def fts_delete(c, pk):
    try:
        c.execute("DELETE FROM msg_fts WHERE rowid=?", (pk,))
    except sqlite3.OperationalError:
        pass


_FLAG_COLS = {"seen", "flagged", "answered", "draft", "deleted"}


def set_local_flags(c, pk, **flags):
    """Mirror IMAP flag changes locally. e.g. set_local_flags(c, pk, seen=1)."""
    cols = {k: int(bool(v)) for k, v in flags.items() if k in _FLAG_COLS}
    if not cols:
        return
    sets = ", ".join(f"{k}=?" for k in cols)
    c.execute(f"UPDATE messages SET {sets} WHERE pk=?", [*cols.values(), pk])


def upsert_message(c, m):
    m = {"confidence": None, "reason": None, **m}  # tolerate callers that omit them
    key = mint_key(c, m["account"], m["message_id"], m["from_addr"], m["date_iso"], m["subject"])
    c.execute("UPDATE message_identity SET gone_at=NULL WHERE key=? AND gone_at IS NOT NULL", (key,))
    cur = c.execute(
        """INSERT INTO messages
           (account,kind,folder,uid,message_id,gmail_thread,from_addr,from_name,
            subject,date_iso,ts,snippet,bucket,confidence,reason,is_junk,message_key)
           VALUES (:account,:kind,:folder,:uid,:message_id,:gmail_thread,:from_addr,
            :from_name,:subject,:date_iso,:ts,:snippet,:bucket,:confidence,:reason,:is_junk,:message_key)
           ON CONFLICT(account,folder,uid) DO UPDATE SET
            subject=excluded.subject, snippet=COALESCE(NULLIF(excluded.snippet, ''), snippet),
            bucket=excluded.bucket, confidence=excluded.confidence,
            reason=excluded.reason, is_junk=excluded.is_junk,
            message_key=COALESCE(message_key, excluded.message_key)
           RETURNING pk""",
        {**m, "message_key": key},
    )
    pk = cur.fetchone()[0]
    fts_index(c, pk)                        # keep the search index in sync on insert + update
    # A copy of a message already assessed needs no second call: moved, moved back or a second folder's.
    c.execute("""INSERT OR IGNORE INTO summaries(message_pk, summary, importance, category, action, model, created_at)
                 SELECT ?, summary, importance, category, action, model, created_at
                 FROM assessments WHERE message_key=?""", (pk, key))
    # And one that was snoozed in this folder, on a row since gone (a resync, a renumber), still is.
    c.execute("""UPDATE messages SET snoozed_until=s.until FROM snoozes s
                 WHERE messages.pk=? AND s.message_key=? AND s.folder=?""", (pk, key, m["folder"]))
    return pk


# ----------------------------------------------------------------------- outbox
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


# ------------------------------------------------------------------- snooze
def set_snooze(c, pk, until):
    """Hide message `pk` until `until`. It's kept under the message's key and folder too, so it outlives
    the row: a copy that arrives in that folder takes it up (upsert_message)."""
    c.execute("UPDATE messages SET snoozed_until=? WHERE pk=?", (int(until), pk))
    c.execute("""INSERT OR REPLACE INTO snoozes(message_key, folder, until)
                 SELECT message_key, folder, snoozed_until FROM messages WHERE pk=? AND message_key IS NOT NULL""",
              (pk,))


def clear_snooze(c, pk):
    c.execute("""DELETE FROM snoozes WHERE (message_key, folder) IN
                 (SELECT message_key, folder FROM messages WHERE pk=?)""", (pk,))
    c.execute("UPDATE messages SET snoozed_until=NULL WHERE pk=?", (pk,))


def due_snoozes(c, now_ts):
    """The rows to wake. A snooze come due with no copy left in its folder ends here: the message
    was filed, and bringing it back later isn't waking it."""
    c.execute("""DELETE FROM snoozes WHERE until<=? AND NOT EXISTS
                 (SELECT 1 FROM messages m WHERE m.message_key=snoozes.message_key AND m.folder=snoozes.folder)""",
              (now_ts,))
    return c.execute("SELECT * FROM messages WHERE snoozed_until IS NOT NULL AND snoozed_until<=?",
                     (now_ts,)).fetchall()


def copy_in_folder(c, message_key, folder):
    """The pk the message with this key has now in `folder`, or None."""
    r = c.execute("SELECT pk FROM messages WHERE message_key=? AND folder=? ORDER BY pk DESC LIMIT 1",
                  (message_key, folder)).fetchone()
    return r["pk"] if r else None


# ----------------------------------------------------------- mute (silence a thread)
def mute_thread(c, account, thread_id, when):
    c.execute("INSERT OR IGNORE INTO muted(account, thread_id, muted_at) VALUES(?,?,?)",
              (account, thread_id, when))


def unmute_thread(c, account, thread_id):
    c.execute("DELETE FROM muted WHERE account=? AND thread_id=?", (account, thread_id))


def is_muted(c, account, thread_id):
    return bool(c.execute("SELECT 1 FROM muted WHERE account=? AND thread_id=?",
                          (account, thread_id)).fetchone())


def muted_thread_seen(c, account, thread_id):
    """Mark every member of a thread \\Seen locally + refresh FTS. Returns [(pk, prev_seen)]
    so the action is undoable. Pure-local (no IMAP)."""
    rows = c.execute("SELECT pk, seen FROM messages WHERE account=? AND thread_id=?",
                     (account, thread_id)).fetchall()
    prev = [(r["pk"], r["seen"]) for r in rows]
    for pk, _ in prev:
        c.execute("UPDATE messages SET seen=1 WHERE pk=?", (pk,))
        fts_index(c, pk)
    return prev


def apply_mute_on_ingest(c, account):
    """After recompute_threads assigns thread_ids, auto-mark unseen members of any muted
    thread \\Seen so freshly-arrived replies in a muted thread stay silent."""
    rows = c.execute("SELECT pk FROM messages WHERE account=? AND seen=0 AND thread_id IN "
                     "(SELECT thread_id FROM muted WHERE account=?)", (account, account)).fetchall()
    for r in rows:
        c.execute("UPDATE messages SET seen=1 WHERE pk=?", (r["pk"],))
        fts_index(c, r["pk"])
    return len(rows)


def list_muted(c, account):
    """One representative (latest) row per muted thread, for the Muted virtual folder."""
    return c.execute(
        """SELECT m.*, (SELECT COUNT(*) FROM messages mm WHERE mm.account=m.account
                          AND mm.thread_id=m.thread_id) AS thread_count
           FROM messages m JOIN muted mu ON mu.account=m.account AND mu.thread_id=m.thread_id
           WHERE m.account=? AND m.pk=(SELECT pk FROM messages m2 WHERE m2.account=m.account
                          AND m2.thread_id=m.thread_id ORDER BY m2.ts DESC, m2.pk DESC LIMIT 1)
           ORDER BY m.ts DESC""", (account,)).fetchall()


def muted_count(c, account):
    # count only muted threads that still have live messages, so the rail badge matches the
    # Muted view (an orphaned muted row whose messages were pruned shows in neither).
    return c.execute(
        "SELECT COUNT(DISTINCT mu.thread_id) FROM muted mu "
        "JOIN messages m ON m.account=mu.account AND m.thread_id=mu.thread_id "
        "WHERE mu.account=?", (account,)).fetchone()[0]


# ----------------------------------------------------------- follow-up nudges
def followup_record_sent(c, account, thread_id, anchor_pk, now_ts, when):
    """Seed a sent-no-reply followup when a reply is sent. INSERT OR IGNORE so a manual
    dismiss or an existing row on the thread is never clobbered by a follow-up send."""
    c.execute("""INSERT OR IGNORE INTO followups(account,thread_id,kind,state,anchor_pk,
                   first_sent_ts,last_msg_ts,created_at) VALUES(?,?,'sent','pending',?,?,?,?)""",
              (account, thread_id, anchor_pk, now_ts, now_ts, when))


def followup_promote_due(c, now_ts, days):
    """Pending sent-no-reply rows past the grace window with no inbound reply since become due."""
    cutoff = now_ts - days * 86400
    c.execute("""UPDATE followups SET state='due', due_ts=? WHERE kind='sent' AND state='pending'
                 AND first_sent_ts <= ?
                 AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.account=followups.account
                       AND m.thread_id=followups.thread_id AND m.ts > followups.first_sent_ts
                       AND lower(m.from_addr) <> lower(followups.account))
                 AND NOT EXISTS (SELECT 1 FROM muted mu WHERE mu.account=followups.account
                       AND mu.thread_id=followups.thread_id)""", (now_ts, cutoff))


def followup_detect_received(c, now_ts, days, max_age_days):
    """Inbound, seen, unanswered, important threads aging past the window with no reply from
    me, not muted/snoozed/already-tracked, within the max-age floor: a nudge expire_followups ended
    must not come straight back, and a first run mustn't flood."""
    cutoff = now_ts - days * 86400
    floor = now_ts - max_age_days * 86400
    c.execute("""INSERT OR IGNORE INTO followups(account,thread_id,kind,state,anchor_pk,last_msg_ts,due_ts,created_at)
        SELECT m.account, m.thread_id, 'received', 'due', m.pk, m.ts, ?, ?
        FROM messages m
        WHERE m.thread_id IS NOT NULL AND m.thread_id != '' AND m.seen=1 AND m.answered=0 AND m.is_junk=0
          AND m.ts <= ? AND m.ts >= ?
          AND m.pk=(SELECT pk FROM messages m2 WHERE m2.account=m.account AND m2.thread_id=m.thread_id
                      AND m2.draft=0 ORDER BY m2.ts DESC, m2.pk DESC LIMIT 1)
          AND m.snoozed_until IS NULL
          AND EXISTS (SELECT 1 FROM messages mi JOIN summaries s ON s.message_pk=mi.pk
                WHERE mi.account=m.account AND mi.thread_id=m.thread_id
                  AND (s.importance>=4 OR s.category='action-needed'))
          AND NOT EXISTS (SELECT 1 FROM messages mo WHERE mo.account=m.account AND mo.draft=0
                AND mo.thread_id=m.thread_id AND lower(mo.from_addr)=lower(m.account))
          AND NOT EXISTS (SELECT 1 FROM muted mu WHERE mu.account=m.account AND mu.thread_id=m.thread_id)
          AND NOT EXISTS (SELECT 1 FROM followups f WHERE f.account=m.account AND f.thread_id=m.thread_id)
        """, (now_ts, datetime.now(timezone.utc).isoformat(), cutoff, floor))


def resolve_followups(c, account):
    """Auto-resolve (delete) active followups whose commitment is met: muted, or — by kind —
    an inbound reply arrived (sent) / I replied or it's marked answered (received). A draft of a
    reply is not a reply."""
    c.execute("""DELETE FROM followups WHERE account=? AND state IN ('pending','due') AND (
        EXISTS (SELECT 1 FROM muted mu WHERE mu.account=followups.account AND mu.thread_id=followups.thread_id)
        OR (followups.kind='sent' AND EXISTS (SELECT 1 FROM messages m WHERE m.account=followups.account
              AND m.thread_id=followups.thread_id AND m.ts > IFNULL(followups.first_sent_ts,0)
              AND lower(m.from_addr) <> lower(followups.account)))
        OR (followups.kind='received' AND EXISTS (SELECT 1 FROM messages m WHERE m.account=followups.account
              AND m.thread_id=followups.thread_id AND (m.answered=1 OR
                  (lower(m.from_addr)=lower(followups.account) AND m.draft=0))))
        OR (followups.kind='replylater' AND EXISTS (SELECT 1 FROM messages m WHERE m.account=followups.account
              AND m.thread_id=followups.thread_id AND (m.answered=1 OR
                  (lower(m.from_addr)=lower(followups.account) AND m.draft=0))))
    )""", (account,))


# ----- Reply Later: a user-pinned stack of threads to answer (kind='replylater') -----
def set_replylater(c, account, thread_id, anchor_pk, when):
    """Pin a thread to the Reply-Later stack. Supersedes any auto-nudge already on the thread
    (an explicit 'I'll reply' beats an aging guess), and re-activates a prior dismissal."""
    c.execute("""INSERT INTO followups(account,thread_id,kind,state,anchor_pk,last_msg_ts,created_at)
                   VALUES(?,?,'replylater','due',?,
                          (SELECT MAX(ts) FROM messages WHERE account=? AND thread_id=? AND draft=0),?)
                 ON CONFLICT(account,thread_id) DO UPDATE SET
                   kind='replylater', state='due', anchor_pk=excluded.anchor_pk,
                   last_msg_ts=excluded.last_msg_ts, created_at=excluded.created_at, resolved_at=NULL""",
              (account, thread_id, anchor_pk, account, thread_id, when))


def clear_replylater(c, account, thread_id):
    """Done / un-pin: drop the reply-later row entirely."""
    c.execute("DELETE FROM followups WHERE account=? AND thread_id=? AND kind='replylater'",
              (account, thread_id))


def list_replylater(c, account):
    """One representative (latest) message per reply-later thread, most-recently-pinned first (a stack).
    A dismissed one has left it, until it is pinned again."""
    return c.execute(
        """SELECT m.*, f.created_at AS f_created,
                  (SELECT COUNT(*) FROM messages mm WHERE mm.account=m.account AND mm.thread_id=m.thread_id) AS thread_count
           FROM followups f JOIN messages m ON m.account=f.account AND m.thread_id=f.thread_id
           WHERE f.account=? AND f.kind='replylater' AND f.state='due'
             AND m.pk=(SELECT pk FROM messages m2 WHERE m2.account=m.account AND m2.thread_id=m.thread_id
                       ORDER BY m2.ts DESC, m2.pk DESC LIMIT 1)
           ORDER BY f.created_at DESC""", (account,)).fetchall()


def replylater_count(c, account):
    # match list_replylater's messages JOIN, so the badge can't exceed the list (a reply-later row
    # whose thread has no surviving message rows after reconcile is not shown, so don't count it)
    return c.execute("SELECT COUNT(*) FROM followups f WHERE f.account=? AND f.kind='replylater' AND f.state='due' "
                     "AND EXISTS(SELECT 1 FROM messages m WHERE m.account=f.account AND m.thread_id=f.thread_id)",
                     (account,)).fetchone()[0]


def dismiss_followup(c, account, thread_id, when):
    """User-dismissed: keep the row as 'dismissed' so detection won't immediately re-add it."""
    c.execute("UPDATE followups SET state='dismissed', resolved_at=? WHERE account=? AND thread_id=?",
              (when, account, thread_id))


def list_waiting(c, account):
    """One representative (latest) message per DUE followup thread, soonest-due first."""
    return c.execute(
        """SELECT m.*, f.kind AS f_kind, f.first_sent_ts AS f_first_sent, f.due_ts AS f_due,
                  (SELECT COUNT(*) FROM messages mm WHERE mm.account=m.account AND mm.thread_id=m.thread_id) AS thread_count
           FROM followups f JOIN messages m ON m.account=f.account AND m.thread_id=f.thread_id
           WHERE f.account=? AND f.state='due' AND f.kind!='replylater'
             AND m.pk=(SELECT pk FROM messages m2 WHERE m2.account=m.account AND m2.thread_id=m.thread_id
                       ORDER BY m2.ts DESC, m2.pk DESC LIMIT 1)
           ORDER BY f.due_ts DESC""", (account,)).fetchall()


def waiting_count(c, account):
    return c.execute(
        "SELECT COUNT(DISTINCT f.thread_id) FROM followups f "
        "JOIN messages m ON m.account=f.account AND m.thread_id=f.thread_id "
        "WHERE f.account=? AND f.state='due' AND f.kind!='replylater'", (account,)).fetchone()[0]


def expire_followups(c, now_ts, days):
    """Bounded lifetime for 'received' nudges: a reply made from ANOTHER client can't be seen
    locally until Sent syncs, so cap how long an unresolved received nudge can nag."""
    c.execute("DELETE FROM followups WHERE kind='received' AND state IN ('pending','due') "
              "AND last_msg_ts IS NOT NULL AND last_msg_ts < ?", (now_ts - days * 86400,))


def due_followup_keys(c):
    """The set of currently-due (account, thread_id) pairs — to detect a swap (count unchanged)."""
    return {(r["account"], r["thread_id"]) for r in
            c.execute("SELECT account, thread_id FROM followups WHERE state='due'").fetchall()}


# ----------------------------------------------------------- thread summaries (AI)
def get_thread_summary(c, account, thread_id, scope="thread"):
    return c.execute("SELECT * FROM thread_summaries WHERE account=? AND thread_id=? AND scope=?",
                     (account, thread_id, scope)).fetchone()


def save_thread_summary(c, row):
    c.execute("""INSERT INTO thread_summaries(account,thread_id,scope,fingerprint,tldr,points,
                   action,participants,message_count,model,created_at)
                 VALUES(:account,:thread_id,:scope,:fingerprint,:tldr,:points,:action,
                   :participants,:message_count,:model,:created_at)
                 ON CONFLICT(account,thread_id,scope) DO UPDATE SET
                   fingerprint=excluded.fingerprint, tldr=excluded.tldr, points=excluded.points,
                   action=excluded.action, participants=excluded.participants,
                   message_count=excluded.message_count, model=excluded.model,
                   created_at=excluded.created_at""", row)


# ------------------------------------------------------------------- briefing card
def get_briefing(c, key):
    return c.execute("SELECT * FROM briefing_cache WHERE key=?", (key,)).fetchone()


def save_briefing(c, key, fingerprint, narrative, groups_json, model, created_at):
    c.execute("""INSERT INTO briefing_cache(key,fingerprint,narrative,groups_json,model,created_at)
                 VALUES(?,?,?,?,?,?)
                 ON CONFLICT(key) DO UPDATE SET fingerprint=excluded.fingerprint,
                   narrative=excluded.narrative, groups_json=excluded.groups_json,
                   model=excluded.model, created_at=excluded.created_at""",
              (key, fingerprint, narrative, groups_json, model, created_at))
    c.execute("DELETE FROM briefing_cache WHERE created_at < ?",   # keep the table tiny
              ((datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),))


# ------------------------------------------------------------------- draft-ahead cache
def draft_ahead_get(c, account, ref_key):
    return c.execute("SELECT * FROM draft_ahead WHERE account=? AND ref_key=?",
                     (account, ref_key)).fetchone()


def draft_ahead_put(c, account, ref_key, fp, body, tone, model, src_pk, src_mid, source, now):
    c.execute("""INSERT INTO draft_ahead(account,ref_key,fp,body,tone,model,source,
                   src_pk,src_message_id,created_ts,last_hit_ts,fail_ts,fail_count)
                 VALUES(?,?,?,?,?,?,?,?,?,?,NULL,0,0)
                 ON CONFLICT(account,ref_key) DO UPDATE SET
                   fp=excluded.fp, body=excluded.body, tone=excluded.tone, model=excluded.model,
                   source=excluded.source, src_pk=excluded.src_pk,
                   src_message_id=excluded.src_message_id, created_ts=excluded.created_ts,
                   fail_ts=0, fail_count=0""",
              (account, ref_key, fp, body, tone, model, source, src_pk, src_mid, now))


def draft_ahead_fail(c, account, ref_key, now):
    """Bump failure fields ONLY — an existing (stale-by-fp) body is left in place."""
    c.execute("""INSERT INTO draft_ahead(account,ref_key,created_ts,fail_ts,fail_count)
                 VALUES(?,?,?,?,1)
                 ON CONFLICT(account,ref_key) DO UPDATE SET fail_ts=?, fail_count=fail_count+1""",
              (account, ref_key, now, now, now))


def draft_ahead_touch(c, account, ref_key, now):
    c.execute("UPDATE draft_ahead SET last_hit_ts=? WHERE account=? AND ref_key=?",
              (now, account, ref_key))


def draft_ahead_gc(c, now, orphans=True):
    """Hygiene only — correctness NEVER depends on GC (the read path revalidates fp/TTL/
    eligibility/anchor on every serve). Orphan sweeps carry a 1h creation grace; pass
    orphans=False from opportunistic (mid-sync-capable) call sites so a reset_folder's
    minutes-long delete+reingest window can never strip live drafts — only the tick
    (which never runs while a sync holds _sync['running']) sweeps orphans."""
    # age-out (drafts AND stale fail markers): 7 days
    c.execute("DELETE FROM draft_ahead WHERE MAX(created_ts, fail_ts) < ?", (now - 7 * 86400,))
    if orphans:
        # orphans: the referenced thread/message no longer exists locally
        c.execute("""DELETE FROM draft_ahead WHERE created_ts < ? AND ref_key LIKE 't:%' AND NOT EXISTS (
                       SELECT 1 FROM messages m WHERE m.account=draft_ahead.account
                         AND m.thread_id=substr(draft_ahead.ref_key,3))""", (now - 3600,))
        c.execute("""DELETE FROM draft_ahead WHERE created_ts < ? AND ref_key LIKE 'm:%' AND NOT EXISTS (
                       SELECT 1 FROM messages m WHERE m.account=draft_ahead.account
                         AND m.message_id=substr(draft_ahead.ref_key,3))""", (now - 3600,))
    # moot: the conversation ended — the thread's latest message is answered OR is the
    # user's own reply (a synced Sent copy becomes the anchor with answered=0, so the
    # anchor's folder is checked too) / the standalone message was answered or junked
    c.execute("""DELETE FROM draft_ahead WHERE body IS NOT NULL AND ref_key LIKE 't:%' AND EXISTS (
                   SELECT 1 FROM messages m WHERE m.account=draft_ahead.account
                     AND m.thread_id=substr(draft_ahead.ref_key,3)
                     AND (m.answered=1 OR lower(m.folder) LIKE '%sent%')
                     AND m.pk=(SELECT m2.pk FROM messages m2 WHERE m2.account=m.account
                               AND m2.thread_id=m.thread_id ORDER BY m2.ts DESC, m2.pk DESC LIMIT 1))""")
    c.execute("""DELETE FROM draft_ahead WHERE body IS NOT NULL AND ref_key LIKE 'm:%' AND EXISTS (
                   SELECT 1 FROM messages m WHERE m.account=draft_ahead.account
                     AND m.message_id=substr(draft_ahead.ref_key,3)
                     AND (m.answered=1 OR m.is_junk=1))""")
    # hard cap 500 rows, LRU-ish (most-recently USEFUL kept: a regeneration counts as
    # fresh via created_ts — COALESCE alone would rank it at its ancient last hit)
    c.execute("""DELETE FROM draft_ahead WHERE (account, ref_key) IN (
                   SELECT account, ref_key FROM draft_ahead
                   ORDER BY MAX(created_ts, COALESCE(last_hit_ts, 0)) DESC LIMIT -1 OFFSET 500)""")


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


def delete_by_uids(c, account, folder, uids):
    """Delete messages (and their summaries) by live UID set — used after a sweep
    moves them out of the INBOX, so they don't linger as ghosts."""
    n = 0
    for uid in uids:
        row = c.execute("SELECT pk FROM messages WHERE account=? AND folder=? AND uid=?",
                        (account, folder, int(uid))).fetchone()
        if row:
            delete_message(c, row["pk"])
            n += 1
    return n


def mark_verified(c, account, folder, live_uids, when):
    """Flag surviving INBOX rows as confirmed-live after a reconcile."""
    live = [int(u) for u in live_uids]
    c.execute("UPDATE messages SET verified=0 WHERE account=? AND folder=?", (account, folder))
    if live:
        qs = ",".join("?" * len(live))
        c.execute(f"""UPDATE messages SET verified=1, last_seen=?
                      WHERE account=? AND folder=? AND uid IN ({qs})""",
                  [when, account, folder, *live])


def reconcile(c, account, folder, live_uids, when, among=None):
    """Prune INBOX rows whose UID is no longer live; stamp survivors verified.
    Returns (pruned, kept). Caller MUST guarantee live_uids came from a real,
    successful SEARCH (never call with an empty set on an errored fetch).
    `among`, when given, holds the UIDs stored before that SEARCH, and only they may be pruned:
    a row a sync stored after it isn't in the SEARCH's answer, but isn't gone."""
    live = set(int(u) for u in live_uids)
    rows = c.execute("SELECT pk, uid FROM messages WHERE account=? AND folder=?",
                     (account, folder)).fetchall()
    pruned_pks = [r["pk"] for r in rows if r["uid"] not in live and (among is None or r["uid"] in among)]
    for pk in pruned_pks:
        delete_message(c, pk)
    mark_verified(c, account, folder, live, when)
    return len(pruned_pks), len(rows) - len(pruned_pks)


def needs_summary(c, limit=200):
    """Non-junk messages without a summary yet, newest first: one copy of each, since save_summary
    gives what the AI says of it to them all."""
    return c.execute(
        """SELECT m.* FROM messages m
           LEFT JOIN summaries s ON s.message_pk = m.pk
           WHERE m.is_junk = 0 AND s.message_pk IS NULL AND m.pending_removal IS NULL
           GROUP BY COALESCE(m.message_key, -m.pk)
           ORDER BY m.ts DESC LIMIT ?""", (limit,)
    ).fetchall()


def save_summary(c, pk, summary, importance, category, action, model, created_at):
    """What the AI said of a message, for this copy, the others and any that comes later."""
    said = (summary, importance, category, action, model, created_at)
    row = c.execute("SELECT message_key FROM messages WHERE pk=?", (pk,)).fetchone()
    key = row["message_key"] if row else None
    copies = [pk] if key is None else [r["pk"] for r in
                                       c.execute("SELECT pk FROM messages WHERE message_key=?", (key,))]
    for copy in copies:
        c.execute(
            """INSERT INTO summaries (message_pk,summary,importance,category,action,model,created_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(message_pk) DO UPDATE SET
                summary=excluded.summary, importance=excluded.importance,
                category=excluded.category, action=excluded.action,
                model=excluded.model, created_at=excluded.created_at""",
            (copy, *said),
        )
    if key is not None:
        c.execute(
            """INSERT INTO assessments (message_key,summary,importance,category,action,model,created_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(message_key) DO UPDATE SET
                summary=excluded.summary, importance=excluded.importance,
                category=excluded.category, action=excluded.action,
                model=excluded.model, created_at=excluded.created_at""",
            (key, *said),
        )


def digest(c, min_importance=1, account=None, limit=300):
    q = """SELECT m.*, s.summary, s.importance, s.category, s.action
           FROM messages m JOIN summaries s ON s.message_pk = m.pk
           WHERE m.is_junk = 0 AND m.snoozed_until IS NULL AND m.pending_removal IS NULL AND s.importance >= ?
             AND NOT EXISTS (SELECT 1 FROM muted mu WHERE mu.account=m.account AND mu.thread_id=m.thread_id)"""
    args = [min_importance]
    if account:
        q += " AND m.account = ?"
        args.append(account)
    q += " ORDER BY s.importance DESC, m.ts DESC LIMIT ?"
    args.append(limit)
    return c.execute(q, args).fetchall()


def stats(c):
    return {
        "total": c.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
        "kept": c.execute("SELECT COUNT(*) FROM messages WHERE is_junk=0").fetchone()[0],
        "summarized": c.execute("SELECT COUNT(*) FROM summaries").fetchone()[0],
        "verified": c.execute("SELECT COUNT(*) FROM messages WHERE verified=1").fetchone()[0],
        "needs": c.execute(
            """SELECT COUNT(*) FROM messages m LEFT JOIN summaries s ON s.message_pk=m.pk
               WHERE m.is_junk=0 AND s.message_pk IS NULL""").fetchone()[0],
    }
