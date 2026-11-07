"""The mail MailMind has stored: messages, their bodies, per-folder sync state, and the command log's rows. Every function but conn and init takes its caller's connection and leaves the commit to it."""
import json
import sqlite3
from datetime import datetime, timezone

from mailmind.content.text import snippet_of
from mailmind.kernel import attention as verdict
from mailmind.mailbox.domain import identity
from mailmind.mailbox.domain.events import ConversationsMerged
from mailmind.mailbox.domain.threading import Copy, Threader
from mailmind.platform import settings
from mailmind.platform.db import database, migrate
from typing import Any


_index: Any = None                               # what keeps the search index up to date: bound by mailmind.bootstrap.mailbox
_merged: Any = None                              # who keeps state under a thread id: bound by mailmind.bootstrap.mailbox


def bind_index(index):
    global _index
    _index = index


def bind_merges(handler):
    """`handler(c, event)` hears each ConversationsMerged, on the connection whose transaction made it. If it raises,
    the merge doesn't happen: the caller's transaction never commits."""
    global _merged
    _merged = handler


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


# ---- the command log's rows; commands.QUEUE moves them through their states ----
def flag_command(c, row, flags, mutation_id, when):
    """A flag change on message `row`, for the server, shown on the row at once, in the caller's transaction. The
    command keeps the flags it replaced, which a change the server refuses, or one given up on, puts back. Returns
    (id, created): created is False if that id was seen already."""
    acct, mid = row["account"], (row["message_id"] or "").strip()
    params = {"pk": row["pk"], "uid": row["uid"], "epoch": get_uidvalidity(c, acct, row["folder"]), "flags": flags,
              "was": {k: bool(row[k]) for k in flags}}
    id_, created = command_insert(c, {"account": acct, "idempotency_key": f"flag:{acct}:{mid or row['pk']}:{mutation_id}",
                                      "kind": "flag", "message_id": mid, "src_folder": row["folder"],
                                      "dest": None, "params": json.dumps(params),
                                      "next_attempt_at": 0, "created_at": when})
    if created:
        set_local_flags(c, row["pk"], **{k: int(bool(v)) for k, v in flags.items()})
    return id_, created


def queue_flags(c, row, flags, mutation_id, when):
    """A flag change for the queue's worker alone, as what another context asks for on its own is. False if that id
    was seen already."""
    return flag_command(c, row, flags, mutation_id, when)[1]


def command_insert(c, row):
    """Insert a queued mutation; (id, created) — created=False on idempotency_key dupe. Race-safe."""
    existing = c.execute("SELECT id FROM mail_commands WHERE idempotency_key=?",
                         (row["idempotency_key"],)).fetchone()
    if existing:
        return existing["id"], False
    try:
        cur = c.execute(
            """INSERT INTO mail_commands(account,idempotency_key,kind,message_id,src_folder,dest,
                 params,state,next_attempt_at,created_at)
               VALUES(:account,:idempotency_key,:kind,:message_id,:src_folder,:dest,
                 :params,'queued',:next_attempt_at,:created_at) RETURNING id""", row)
        return cur.fetchone()["id"], True
    except sqlite3.IntegrityError:
        r = c.execute("SELECT id FROM mail_commands WHERE idempotency_key=?",
                      (row["idempotency_key"],)).fetchone()
        return (r["id"] if r else None), False


def command_get(c, mid):
    return c.execute("SELECT * FROM mail_commands WHERE id=?", (mid,)).fetchone()


def command_mark_begun(c, mid):
    """A try at it may reach the server from here on: the next one asks the server before it repeats anything."""
    c.execute("UPDATE mail_commands SET begun=1 WHERE id=?", (mid,))


def command_never_begun(c, mid):
    c.execute("UPDATE mail_commands SET begun=0 WHERE id=?", (mid,))


def command_record(c, mid, dest, outcome):
    """What the server's answer left for a reversal to know: where the message went, and what else."""
    c.execute("UPDATE mail_commands SET dest=COALESCE(?, dest), outcome=? WHERE id=?", (dest, outcome, mid))


def command_set_params(c, mid, params):
    c.execute("UPDATE mail_commands SET params=? WHERE id=?", (params, mid))


def command_open(c, limit):
    """The changes that haven't reached a server, newest first: waiting, being tried or given up."""
    return c.execute("SELECT * FROM mail_commands WHERE applied=0 AND state IN ('queued', 'running', 'error') "
                     "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def command_open_counts(c):
    """{state: how many} of the changes that haven't reached a server."""
    return {r["state"]: r["n"] for r in c.execute(
        "SELECT state, COUNT(*) n FROM mail_commands WHERE applied=0 "
        "AND state IN ('queued', 'running', 'error') GROUP BY state")}


def command_forget_finished(c, now_ts, days=30):
    """A queue row finished `days` ago goes: nothing resubmits or undoes it after that. A row still
    open, or in `error` waiting for a person, stays."""
    cutoff = datetime.fromtimestamp(now_ts - days * 86400, timezone.utc).isoformat()
    return c.execute("DELETE FROM mail_commands WHERE state='done' AND done_at < ?", (cutoff,)).rowcount


def command_latest_move(c, account, message_id):
    """The newest move of a message, in any state: what an undo entry written before entries named their command
    goes by, to decide between a cancel and a counter-move and to find where the message went."""
    return c.execute("SELECT * FROM mail_commands WHERE account=? AND message_id=? AND kind!='flag' "
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
    command path uses to re-resolve a message whose pk/uid may have changed via reconcile."""
    if not message_id:
        return None
    return c.execute("SELECT * FROM messages WHERE account=? AND message_id=? ORDER BY pk DESC LIMIT 1",
                     (account, message_id)).fetchone()


def delete_message(c, pk):
    row = c.execute("SELECT message_key FROM messages WHERE pk=?", (pk,)).fetchone()
    _index.forget_message(c, pk)                       # drop the FTS row first (single delete chokepoint)
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
    """Leave message `pk` out of every list while the move `mutation_id` (a mail_commands id) takes it
    away. The row stays, so a move that doesn't happen can list it again."""
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
RECIPIENT_ROWS_MAX = 5000
RECIPIENTS_CHARS: int = 20000     # of the To and Cc text kept for search: a long list, not a forged one's whole length


def set_recipients(c, pk, addrs):
    """Who this copy of a message went to, one row each (`mime.recipient_addrs`). Bounded, since a
    forged To can be as long as it likes."""
    c.execute("DELETE FROM message_recipients WHERE message_pk=?", (pk,))
    c.executemany("INSERT OR IGNORE INTO message_recipients(message_pk, addr) VALUES (?,?)",
                  [(pk, a) for a in addrs[:RECIPIENT_ROWS_MAX]])


def recipients_of(parsed):
    """To + Cc as one searchable string (names + addresses), for the to:/cc: operators."""
    return ((parsed.get("to") or "") + " " + (parsed.get("cc") or "")).strip()[:RECIPIENTS_CHARS]


def fts_set_body(c, pk, body_text):
    """Fill body_text once a body is fetched, derive a list/river snippet if none was
    captured at ingest, and refresh the FTS row."""
    bt = body_text or ""
    snip = snippet_of(bt)
    c.execute("UPDATE messages SET body_text=?, "
              "snippet=CASE WHEN snippet IS NULL OR snippet='' THEN ? ELSE snippet END "
              "WHERE pk=?", (bt, snip, pk))
    _index.index_message(c, pk)


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
    _index.index_message(c, pk)                        # keep the search index in sync on insert + update
    # A copy of a message already assessed needs no second call: moved, moved back or a second folder's.
    c.execute("""INSERT OR IGNORE INTO summaries(message_pk, summary, importance, category, action, model, created_at)
                 SELECT ?, summary, importance, category, action, model, created_at
                 FROM assessments WHERE message_key=?""", (pk, key))
    # And one that was snoozed in this folder, on a row since gone (a resync, a renumber), still is.
    c.execute("""UPDATE messages SET snoozed_until=s.until FROM snoozes s
                 WHERE messages.pk=? AND s.message_key=? AND s.folder=?""", (pk, key, m["folder"]))
    return pk


def copy_in_folder(c, message_key, folder):
    """The pk the message with this key has now in `folder`, or None."""
    r = c.execute("SELECT pk FROM messages WHERE message_key=? AND folder=? ORDER BY pk DESC LIMIT 1",
                  (message_key, folder)).fetchone()
    return r["pk"] if r else None


def pk_now(c, key, folder):
    """The pk a stored answer's message has now, or None if it has gone. Among its copies, the one in
    the folder it was read from."""
    row = c.execute("SELECT pk FROM messages WHERE message_key=? AND pending_removal IS NULL "
                    "ORDER BY folder=? DESC, pk DESC LIMIT 1", (key, folder)).fetchone()
    return row["pk"] if row else None


def pk_in(c, key, folder):
    """The pk of a message's copy in this folder now, or None if none is there or it is on its way out."""
    row = c.execute("SELECT pk FROM messages WHERE message_key=? AND folder=? AND pending_removal IS NULL "
                    "ORDER BY pk DESC LIMIT 1", (key, folder)).fetchone()
    return row["pk"] if row else None


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


# ------------------------------------------------------------------- what the Assistant reads (its Mail port)
def _among(accounts, col):
    """A clause confining a query to `accounts`, and its parameters: nothing when none are named."""
    if not accounts:
        return "", []
    return " AND %s IN (%s) " % (col, ",".join("?" for _ in accounts)), list(accounts)


def brief_rows(c, accounts, n):
    """What a brief is made from: the mail the AI said is worth a glance, most important first."""
    sql = ("SELECT m.pk, m.message_key, m.account, m.folder, m.from_name, m.from_addr, m.subject, m.message_id, "
           "s.summary, s.importance, s.category, s.action "
           "FROM messages m JOIN summaries s ON s.message_pk=m.pk "
           "WHERE m.is_junk=0 AND m.snoozed_until IS NULL AND m.pending_removal IS NULL AND s.importance>=2 "
           "AND NOT EXISTS (SELECT 1 FROM muted mu WHERE mu.account=m.account AND mu.thread_id=m.thread_id) ")
    params = []
    if accounts:
        sql += "AND m.account IN (%s) " % ",".join("?" for _ in accounts)
        params += list(accounts)
    sql += "ORDER BY s.importance DESC, m.ts DESC LIMIT ?"
    params.append(n)
    return c.execute(sql, params).fetchall()


def delta_rows(c, accounts, since_ts, n):
    """What a delta is made from: the mail newer than `since_ts`, newest first."""
    sql = ("SELECT m.pk, m.account, m.from_name, m.from_addr, m.subject, m.ts, m.message_id, "
           "COALESCE(s.summary,'') AS summary, COALESCE(s.importance,2) AS importance, "
           "COALESCE(s.category,'other') AS category, COALESCE(s.action,0) AS action "
           "FROM messages m LEFT JOIN summaries s ON s.message_pk=m.pk "
           # a delta is "what changed" — include newsletters/notifications (the AI buckets them as noise)
           # so it matches the pill count; only drop spam/blocked and snoozed/muted.
           "WHERE (m.bucket IS NULL OR m.bucket NOT IN ('spam/scam','blocked')) "
           "AND m.snoozed_until IS NULL AND m.pending_removal IS NULL AND m.ts > ? "
           "AND NOT EXISTS (SELECT 1 FROM muted mu WHERE mu.account=m.account AND mu.thread_id=m.thread_id) ")
    params = [since_ts]
    if accounts:
        sql += "AND m.account IN (%s) " % ",".join("?" for _ in accounts)
        params += list(accounts)
    sql += "ORDER BY m.ts DESC LIMIT ?"       # newest first
    params.append(n)
    return c.execute(sql, params).fetchall()


def sent_since(c, accounts, since_ts):
    ac, ap = _among(accounts, "account")
    return c.execute(
        "SELECT COUNT(*) FROM messages WHERE lower(folder) LIKE '%sent%' AND ts>=?" + ac,
        [since_ts, *ap]).fetchone()[0]


def received_since(c, accounts, since_ts):
    ac, ap = _among(accounts, "account")
    return c.execute(
        "SELECT COUNT(*) FROM messages WHERE folder='INBOX' AND ts>=?" + ac,
        [since_ts, *ap]).fetchone()[0]


def received_by_bucket(c, accounts, since_ts):
    """What kind of mail arrived: each bucket, and how many."""
    ac, ap = _among(accounts, "account")
    return c.execute(
        "SELECT bucket, COUNT(*) n FROM messages WHERE folder='INBOX' AND ts>=?" + ac
        + " GROUP BY bucket", [since_ts, *ap]).fetchall()


def needs_you_rows(c, accounts, n):
    """Who's waiting on the person to reply: open, important, unanswered INBOX mail (mirrors the brief's "needs you"
    set; joins summaries so we get importance/action, excludes junk/snoozed/muted/answered)."""
    ac, ap = _among(accounts, "m.account")
    return c.execute(
        "SELECT m.pk, m.message_key, m.folder, m.account, m.thread_id, m.from_name, m.from_addr, m.subject, m.ts, "
        "       s.importance, s.action, s.category "
        "FROM messages m JOIN summaries s ON s.message_pk=m.pk "
        "WHERE m.folder='INBOX' AND m.is_junk=0 AND m.snoozed_until IS NULL AND m.answered=0 "
        "  AND m.pending_removal IS NULL "
        f"  AND {verdict.needs_you_sql()} "
        "  AND NOT EXISTS (SELECT 1 FROM muted mu WHERE mu.account=m.account AND mu.thread_id=m.thread_id) "
        + ac + " ORDER BY s.importance DESC, m.ts DESC LIMIT ?", [*ap, n]).fetchall()


_DRAFT_CAND_SQL = """
SELECT m.pk, m.account, m.folder, m.thread_id, m.message_id, m.from_addr,
       m.is_junk, m.answered, m.snoozed_until, m.ts, s.importance, s.category
FROM messages m
JOIN summaries s ON s.message_pk = m.pk
WHERE m.folder = 'INBOX'
  AND m.is_junk = 0 AND m.answered = 0 AND m.snoozed_until IS NULL AND m.pending_removal IS NULL
  AND """ + verdict.reply_worthy_sql() + """
  AND m.ts >= ?
  AND (COALESCE(m.thread_id,'') <> '' OR COALESCE(m.message_id,'') <> '')
  AND (COALESCE(m.thread_id,'') = '' OR m.pk = (
        SELECT m2.pk FROM messages m2 WHERE m2.account = m.account
          AND m2.thread_id = m.thread_id ORDER BY m2.ts DESC, m2.pk DESC LIMIT 1))
  AND NOT EXISTS (SELECT 1 FROM muted mu
        WHERE mu.account = m.account AND mu.thread_id = m.thread_id)
ORDER BY s.importance DESC, m.ts DESC
LIMIT 40
"""


def draft_candidates(c, since_ts):
    """What drafting ahead may pick from: reply-worthy Inbox mail since then, each thread by its latest message, most
    important first."""
    return c.execute(_DRAFT_CAND_SQL, (since_ts,)).fetchall()


def thread_stamps(c, account, thread_id):
    """Each copy's Message-ID and time: the thread's state, for a reply drafted ahead."""
    return c.execute("SELECT message_id, ts FROM messages WHERE account=? AND thread_id=?",
                     (account, thread_id)).fetchall()


def message_stamp(c, pk):
    """A message's Message-ID and whether its body is here: a lone message's state, for a reply drafted ahead."""
    return c.execute("SELECT message_id, body_fetched FROM messages WHERE pk=?", (pk,)).fetchone()


def thread_anchor_pk(c, account, thread_id):
    """The thread's latest message — the only pk a thread draft may be served/kicked for
    (never pair the thread-latest draft with a composer quoting an older message)."""
    r = c.execute("SELECT pk FROM messages WHERE account=? AND thread_id=? "
                  "ORDER BY ts DESC, pk DESC LIMIT 1", (account, thread_id)).fetchone()
    return r["pk"] if r else None


def conversation_rows(c, account, by, value):
    """A conversation's copies, oldest first. `by` names what they share: the column `thread_id`, or for a message with
    no thread its `message_id` or `pk`, never anything a request wrote."""
    return c.execute(
        f"""SELECT pk,account,from_name,from_addr,date_iso,subject,snippet,body_text,message_id,body_fetched,ts
           FROM messages WHERE account=? AND {by}=? ORDER BY ts ASC, pk ASC""",
        (account, value)).fetchall()


def message_to_reply_to(c, pk):
    """A message with no thread as a reply is drafted from it, in a list as a conversation's are."""
    return c.execute("SELECT pk,account,from_name,from_addr,date_iso,subject,snippet,body_text,message_id,"
                     "body_fetched FROM messages WHERE pk=?", (pk,)).fetchall()


def any_copy(c, message_key):
    """One stored copy of a message, whichever comes first, or None if none is left."""
    return c.execute("SELECT * FROM messages WHERE message_key=? LIMIT 1", (message_key,)).fetchone()


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


# ---- what the page lists: none holds a row on its way out (pending_removal) ----
_MUTED_EXCL = ("AND NOT EXISTS (SELECT 1 FROM muted mu WHERE mu.account={t}.account "
               "AND mu.thread_id={t}.thread_id)")   # mirror the snoozed_until IS NULL exclusion


def snoozed_rows(c, account, limit, offset):
    """Every snoozed message in the account, soonest wake first."""
    return c.execute(
        """SELECT m.pk, m.message_key, m.folder, m.from_addr, m.from_name, m.subject, m.date_iso, m.snippet,
                  m.seen, m.flagged, m.has_attach, m.is_junk, m.bucket, m.thread_id,
                  m.snoozed_until, s.importance, s.category, s.action
           FROM messages m LEFT JOIN summaries s ON s.message_pk=m.pk
           WHERE m.account=? AND m.snoozed_until IS NOT NULL AND m.pending_removal IS NULL
           ORDER BY m.snoozed_until ASC LIMIT ? OFFSET ?""", (account, limit, offset)).fetchall()


def snoozed_count(c, account):
    return c.execute("SELECT COUNT(*) FROM messages WHERE account=? AND snoozed_until IS NOT NULL "
                     "AND pending_removal IS NULL",
                     (account,)).fetchone()[0]


def folder_conversation_rows(c, account, folder, limit, offset):
    """A folder collapsed to conversations: one row per thread (the latest message) + rollups."""
    return c.execute(
        """WITH fm AS (
             SELECT m.*, s.importance, s.summary, s.action, s.category,
               ROW_NUMBER() OVER (PARTITION BY m.thread_id ORDER BY m.ts DESC, m.pk DESC) rn,
               COUNT(*)        OVER (PARTITION BY m.thread_id) cnt,
               SUM(CASE WHEN m.seen=0 THEN 1 ELSE 0 END) OVER (PARTITION BY m.thread_id) unread,
               MAX(m.flagged)  OVER (PARTITION BY m.thread_id) any_flagged,
               MAX(m.has_attach) OVER (PARTITION BY m.thread_id) any_attach
             FROM messages m LEFT JOIN summaries s ON s.message_pk=m.pk
             WHERE m.account=? AND m.folder=? AND m.snoozed_until IS NULL AND m.pending_removal IS NULL """
        + _MUTED_EXCL.format(t="m") + """)
           SELECT * FROM fm WHERE rn=1 ORDER BY ts DESC LIMIT ? OFFSET ?""",
        (account, folder, limit, offset)).fetchall()


def folder_conversation_count(c, account, folder):
    return c.execute("SELECT COUNT(DISTINCT thread_id) FROM messages "
                     "WHERE account=? AND folder=? AND snoozed_until IS NULL AND pending_removal IS NULL "
                     + _MUTED_EXCL.format(t="messages"),
                     (account, folder)).fetchone()[0]


def folder_rows(c, account, folder, limit, offset):
    """A folder's messages, newest first."""
    return c.execute(
        """SELECT m.pk, m.message_key, m.from_addr, m.from_name, m.subject, m.date_iso, m.ts, m.snippet,
                  m.seen, m.flagged, m.has_attach, m.is_junk, m.bucket, m.thread_id, s.importance,
                  s.summary, s.action, s.category
           FROM messages m LEFT JOIN summaries s ON s.message_pk=m.pk
           WHERE m.account=? AND m.folder=? AND m.snoozed_until IS NULL AND m.pending_removal IS NULL """
        + _MUTED_EXCL.format(t="m") + """
           ORDER BY m.ts DESC LIMIT ? OFFSET ?""",
        (account, folder, limit, offset)).fetchall()


def folder_count(c, account, folder):
    return c.execute("SELECT COUNT(*) FROM messages "
                     "WHERE account=? AND folder=? AND snoozed_until IS NULL AND pending_removal IS NULL "
                     + _MUTED_EXCL.format(t="messages"),
                     (account, folder)).fetchone()[0]


def stored_folder_counts(c, account):
    """{folder: (unseen, total)} of what is stored for the account, each folder a sync has read among them."""
    counts = {r["folder"]: (r["unseen"], r["total"]) for r in c.execute(
        "SELECT folder, SUM(seen=0) AS unseen, COUNT(*) AS total FROM messages WHERE account=? "
        "AND pending_removal IS NULL GROUP BY folder", (account,))}
    for r in c.execute("SELECT folder FROM sync_state WHERE account=?", (account,)):
        counts.setdefault(r["folder"], (0, 0))
    return counts


def thread_rows(c, account, thread_id):
    """A conversation's messages in the account, oldest first, every copy of each."""
    return c.execute(
        """SELECT pk, message_key, folder, from_addr, from_name, subject, date_iso, ts, snippet,
                  seen, flagged, answered, has_attach, is_junk, message_id
           FROM messages WHERE account=? AND thread_id=? AND pending_removal IS NULL
           ORDER BY ts ASC, pk ASC""",
        (account, thread_id)).fetchall()


_THREADING = "pk, message_id, gmail_thread, in_reply_to, refs, thread_id, folder, ts"
_SLICE = 500                                     # how many values one IN names


def _copy(r):
    return Copy(r["pk"], r["message_id"], r["gmail_thread"], r["in_reply_to"], r["refs"], r["thread_id"], r["folder"], r["ts"])


def _slices(values):
    values = sorted(values)
    for start in range(0, len(values), _SLICE):
        part = values[start:start + _SLICE]
        yield part, ",".join("?" * len(part))


def _threads_naming(c, account, names):
    """For each of `names`, the ids of the threads holding a copy that names it. CROSS JOIN keeps the planner from
    starting at the account's messages."""
    touching = {}
    for part, marks in _slices(names):
        for r in c.execute(f"""SELECT DISTINCT l.name, m.thread_id FROM thread_links l CROSS JOIN messages m ON m.pk=l.message_pk
                               WHERE l.name IN ({marks}) AND m.account=? AND m.thread_id IS NOT NULL""", [*part, account]):
            touching.setdefault(r["name"], set()).add(r["thread_id"])
    return touching


def _write_links(c, copies):
    """The ids each of these copies names, as it names them now. See migration 42."""
    c.executemany("DELETE FROM thread_links WHERE message_pk=?", [(m.pk,) for m in copies])
    c.executemany("INSERT OR IGNORE INTO thread_links(name, message_pk) VALUES (?,?)",
                  [(name, m.pk) for m in copies for name in m.names()])


def needs_threading(c, pk, in_reply_to, refs):
    """Whether a copy about to hold these references has anything for threading to do: it has no id yet, or they
    aren't the ones it holds. A full rescan stores every copy again, and threading hears of none of them."""
    r = c.execute("SELECT thread_id, in_reply_to, refs FROM messages WHERE pk=?", (pk,)).fetchone()
    return not r or not r["thread_id"] or (r["in_reply_to"] or "", r["refs"] or "") != (in_reply_to, refs)


def thread_stored(c, account, pks=()):
    """Give what a sync stored its thread id: the copies named, and any of the account's with no id yet, since a sync that
    died before this point or `mailmind sync` left them so. It reads what they name and the threads that touches, and
    rewrites only those. A brand-new thread's id comes from all that arrived for it here, which is why it runs once
    per sync and not for each message. In the caller's transaction, which it opens if there's work: what it reads
    decides what it writes, so no other writer may come between."""
    pks = list(pks)
    if not pks and not c.execute("SELECT 1 FROM messages INDEXED BY idx_msg_thread WHERE thread_id IS NULL AND account=? LIMIT 1",
                                 (account,)).fetchone():
        return
    if not c.in_transaction:
        c.execute("BEGIN IMMEDIATE")
    arrivals = {r["pk"]: _copy(r) for r in c.execute(
        f"SELECT {_THREADING} FROM messages INDEXED BY idx_msg_thread WHERE thread_id IS NULL AND account=?", (account,))}
    for part, marks in _slices(pks):
        arrivals.update((r["pk"], _copy(r)) for r in c.execute(
            f"SELECT {_THREADING} FROM messages WHERE pk IN ({marks}) AND account=?", [*part, account]))
    threader = Threader(account)
    conversations = threader.conversations(
        arrivals.values(), _threads_naming(c, account, {name for m in arrivals.values() for name in m.names()}))
    members = []
    for part, marks in _slices(threader.contested(conversations)):
        members += [_copy(r) for r in c.execute(
            f"SELECT {_THREADING} FROM messages INDEXED BY idx_msg_thread WHERE thread_id IN ({marks}) AND account=?", [*part, account])]
    placed = threader.place(conversations, members)
    c.executemany("UPDATE messages SET thread_id=? WHERE pk=?",
                  [(placed.given[m.pk], m.pk) for m in arrivals.values() if m.thread_id != placed.given[m.pk]])
    _write_links(c, list(arrivals.values()))
    for absorbed, survivor in placed.merged.items():
        c.execute("UPDATE messages INDEXED BY idx_msg_thread SET thread_id=? WHERE thread_id=? AND account=?",
                  (survivor, absorbed, account))
        _merged(c, ConversationsMerged(account, survivor, absorbed))


def recompute_threads(c, account):
    """The repair (`python3 -m mailmind db rethread`): regroup every message of the account from what each names, and
    write what each names again. No sync comes this way (thread_stored), and nothing has to."""
    if not c.in_transaction:
        c.execute("BEGIN IMMEDIATE")
    copies = [_copy(r) for r in c.execute(f"SELECT {_THREADING} FROM messages WHERE account=?", (account,))]
    placed = Threader(account).regroup(copies)
    for pk, tid in placed.given.items():
        c.execute("UPDATE messages SET thread_id=? WHERE pk=? AND (thread_id IS NULL OR thread_id<>?)", (tid, pk, tid))
    _write_links(c, copies)
    for absorbed, survivor in placed.merged.items():
        _merged(c, ConversationsMerged(account, survivor, absorbed))
