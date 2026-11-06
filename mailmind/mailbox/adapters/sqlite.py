"""The mail MailMind has stored: messages, their bodies, per-folder sync state, the offline queue's rows and
the undo stack. Every function but conn and init takes its caller's connection and leaves the commit to it."""
import json
import sqlite3
from datetime import datetime, timezone

from mailmind.content.text import snippet_of
from mailmind.mailbox.domain import conversations, identity
from mailmind.platform import settings
from mailmind.platform.db import database, migrate
from typing import Any


_index: Any = None                               # what keeps the search index up to date: bound by mailmind.bootstrap.mailbox


def bind_index(index):
    global _index
    _index = index


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


# ---- offline read-mutation queue: the rows; offline.QUEUE moves them through their states ----
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


def read_mut_forget_finished(c, now_ts, days=30):
    """A queue row finished `days` ago goes: nothing resubmits or undoes it after that. A row still
    open, or in `error` waiting for a person, stays."""
    cutoff = datetime.fromtimestamp(now_ts - days * 86400, timezone.utc).isoformat()
    return c.execute("DELETE FROM read_mutations WHERE state='done' AND done_at < ?", (cutoff,)).rowcount


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
RECIPIENT_ROWS_MAX = 5000
RECIPIENTS_CHARS: int = 20000     # of the To and Cc text kept for search: a long list, not a forged one's whole length


def set_recipients(c, pk, addrs):
    """Who this copy of a message went to, one row each (`mime.recipient_addrs`). Bounded, since a
    forged To can be as long as it likes."""
    c.execute("DELETE FROM message_recipients WHERE message_pk=?", (pk,))
    c.executemany("INSERT OR IGNORE INTO message_recipients(message_pk, addr) VALUES (?,?)",
                  [(pk, a) for a in addrs[:RECIPIENT_ROWS_MAX]])


def _recipients_of(parsed):
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


def recompute_threads(c, account):
    """Give each of the account's messages its thread id, and move what was kept under an id no row holds
    any more (see mailmind.mailbox.domain.conversations)."""
    rows = c.execute(
        "SELECT pk, message_id, gmail_thread, in_reply_to, refs, thread_id "
        "FROM messages WHERE account=?", (account,)).fetchall()
    if not rows:
        return
    given, moved = conversations.assign(rows, account)
    for pk, tid in given.items():
        c.execute("UPDATE messages SET thread_id=? WHERE pk=? AND (thread_id IS NULL OR thread_id<>?)", (tid, pk, tid))
    for old, surv in moved.items():
        _rekey(c, account, old, surv)


def _rekey(c, account, old, surv):
    """Move the state kept under thread `old` to `surv`. Where `surv` has a row of its own already,
    that row stays and `old`'s goes (UPDATE OR IGNORE, then DELETE)."""
    for tbl in ("muted", "followups", "thread_summaries"):
        try:
            c.execute(f"UPDATE OR IGNORE {tbl} SET thread_id=? WHERE account=? AND thread_id=?",
                      (surv, account, old))
            c.execute(f"DELETE FROM {tbl} WHERE account=? AND thread_id=?", (account, old))
        except Exception:
            pass                              # table may not exist yet on a pre-migration DB
    try:
        c.execute("UPDATE OR IGNORE draft_ahead SET ref_key=? WHERE account=? AND ref_key=?",
                  ("t:" + surv, account, "t:" + old))
        c.execute("DELETE FROM draft_ahead WHERE account=? AND ref_key=?", (account, "t:" + old))
        c.execute("UPDATE outbox SET orig_thread_id=? WHERE orig_account=? AND orig_thread_id=?", (surv, account, old))
    except Exception:
        pass
