"""A message's rows: finding one, its copies and its key, the rows on their way out (`pending_removal`), and what a reconcile prunes."""
from datetime import datetime, timezone

from mailmind.mailbox.adapters.sqlite import bound
from mailmind.mailbox.domain import identity, prune


def set_list_unsub(c, pk, raw, method):
    c.execute("UPDATE messages SET list_unsub_raw=?, list_unsub_method=? WHERE pk=?", (raw, method, pk))


def get_message(c, pk):
    """A message's row, and `snooze_until`, when `snoozes` says it wakes, if it's snoozed."""
    return c.execute("""SELECT m.*, z.until AS snooze_until FROM messages m
                        LEFT JOIN snoozes z ON z.message_key=m.message_key AND z.folder=m.folder
                        WHERE m.pk=?""", (pk,)).fetchone()


def get_message_by_msgid(c, account, message_id):
    """Latest local row for a message by (account, RFC822 Message-ID) — the durable key the
    command path uses to re-resolve a message whose pk/uid may have changed via reconcile."""
    if not message_id:
        return None
    return c.execute("SELECT * FROM messages WHERE account=? AND message_id=? ORDER BY pk DESC LIMIT 1",
                     (account, message_id)).fetchone()


def get_message_by_key(c, account, key):
    """Latest local row for a message by its key: a message with no Message-ID has that to be found by."""
    return c.execute("SELECT * FROM messages WHERE account=? AND message_key=? ORDER BY pk DESC LIMIT 1",
                     (account, key)).fetchone()


def newest_copy_in(c, account, folder, message_id):
    """The pk, as a row, of the newest copy of a message in `folder`, on its way out or not; None if there is none."""
    return c.execute("SELECT pk FROM messages WHERE account=? AND folder=? AND message_id=? "
                     "ORDER BY pk DESC LIMIT 1", (account, folder, message_id)).fetchone()


def has_copy_in(c, account, folder, message_id):
    """Whether a copy of a message is in `folder`, on its way out or not."""
    return c.execute("SELECT 1 FROM messages WHERE account=? AND folder=? AND message_id=?",
                     (account, folder, message_id)).fetchone() is not None


def is_listed_in(c, account, folder, message_id):
    """Whether a copy of a message is in `folder` and not on its way out."""
    return c.execute("SELECT 1 FROM messages WHERE account=? AND folder=? AND message_id=? "
                     "AND pending_removal IS NULL", (account, folder, message_id)).fetchone() is not None


def thread_pks_in(c, account, folder, thread_id):
    """The pks of a conversation's messages in `folder`, none on its way out, newest first."""
    return [r["pk"] for r in c.execute(
        "SELECT pk FROM messages WHERE account=? AND folder=? AND thread_id=? "
        "AND pending_removal IS NULL ORDER BY ts DESC, pk DESC", (account, folder, thread_id))]


def delete_message(c, pk):
    row = c.execute("SELECT message_key FROM messages WHERE pk=?", (pk,)).fetchone()
    bound._index.forget_message(c, pk)                       # drop the FTS row first (single delete chokepoint)
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


def is_known(c, account, message_id, from_addr, date_iso, subject):
    """Whether any copy of this message has been seen before: what `mint_key` would find."""
    who = (account, identity.of(message_id, from_addr, date_iso, subject))
    return c.execute("SELECT 1 FROM message_identity WHERE account=? AND identity=?", who).fetchone() is not None


def repair_keys(c):
    """What only a run of an older commit leaves behind. A row with no key gets one, and a key it comes back to is no
    longer gone."""
    rows = c.execute("SELECT pk, account, message_id, from_addr, date_iso, subject FROM messages "
                     "WHERE message_key IS NULL").fetchall()
    for r in rows:
        key = mint_key(c, r["account"], r["message_id"], r["from_addr"], r["date_iso"], r["subject"])
        c.execute("UPDATE message_identity SET gone_at=NULL WHERE key=? AND gone_at IS NOT NULL", (key,))
        c.execute("UPDATE messages SET message_key=? WHERE pk=?", (key, r["pk"]))


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
    bound._index.index_message(c, pk)                        # keep the search index in sync on insert + update
    return pk


def copy_in_folder(c, message_key, folder):
    """The pk the message with this key has now in `folder`, or None."""
    r = c.execute("SELECT pk FROM messages WHERE message_key=? AND folder=? ORDER BY pk DESC LIMIT 1",
                  (message_key, folder)).fetchone()
    return r["pk"] if r else None


def copy_now(c, key, folder):
    """The copy a stored answer's message has now, its pk, folder and uid, or None if it has gone. Among its copies, the one
    in the folder it was read from."""
    return c.execute("SELECT pk, folder, uid FROM messages WHERE message_key=? AND pending_removal IS NULL "
                     "ORDER BY folder=? DESC, pk DESC LIMIT 1", (key, folder)).fetchone()


def copy_pk(c, key, folder, uid=None):
    """The pk the copy of a message in `folder` has now, or None. A uid says which: the row of that key in that folder at
    that uid, and none if it isn't there, since two rows in one folder can share a key (a sender who reuses a Message-ID,
    or a list's copy beside the one sent straight). Without one, the only copy there, and none where there are two, rather
    than a guess."""
    if uid is not None:
        row = c.execute("SELECT pk FROM messages WHERE message_key=? AND folder=? AND uid=?", (key, folder, uid)).fetchone()
        return row["pk"] if row else None
    rows = c.execute("SELECT pk FROM messages WHERE message_key=? AND folder=? LIMIT 2", (key, folder)).fetchall()
    return rows[0]["pk"] if len(rows) == 1 else None


def message_pk(c, key, folder=None, uid=None):
    """The pk of a copy of the message now, or None if it has none: the copy at `uid` in `folder` where that is one of
    its copies, else its copy in `folder` if there is one, else another, one listed first, then the newer."""
    if uid is not None and folder is not None and (pk := copy_pk(c, key, folder, uid)) is not None:
        return pk
    row = c.execute("SELECT pk FROM messages WHERE message_key=? "
                    "ORDER BY folder IS ? DESC, pending_removal IS NULL DESC, pk DESC LIMIT 1", (key, folder)).fetchone()
    return row["pk"] if row else None


def names_of(c, pks):
    """{pk: (key, folder, uid)} for each of `pks` that has a row: what names those copies on the wire."""
    out = {}
    for pk in pks:
        row = c.execute("SELECT message_key, folder, uid FROM messages WHERE pk=?", (pk,)).fetchone()
        if row:
            out[pk] = (row["message_key"], row["folder"], row["uid"])
    return out


def pk_in(c, key, folder):
    """The pk of a message's copy in this folder now, or None if none is there or it is on its way out."""
    row = c.execute("SELECT pk FROM messages WHERE message_key=? AND folder=? AND pending_removal IS NULL "
                    "ORDER BY pk DESC LIMIT 1", (key, folder)).fetchone()
    return row["pk"] if row else None


def delete_by_uids(c, account, folder, uids):
    """Delete messages by live UID set — used after a sweep
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


def stored_uids(c, account, folder):
    return [r["uid"] for r in c.execute(
        "SELECT uid FROM messages WHERE account=? AND folder=?", (account, folder))]


def reconcile(c, account, folder, live_uids, when, among=None):
    """Prune INBOX rows whose UID is no longer live; stamp survivors verified.
    Returns (pruned, kept). Caller MUST guarantee live_uids came from a real,
    successful SEARCH (never call with an empty set on an errored fetch).
    `among`, when given, holds the UIDs stored before that SEARCH, and only they may be pruned:
    a row a sync stored after it isn't in the SEARCH's answer, but isn't gone."""
    live = set(int(u) for u in live_uids)
    rows = c.execute("SELECT pk, uid FROM messages WHERE account=? AND folder=?",
                     (account, folder)).fetchall()
    pruned_pks = [r["pk"] for r in rows if prune.goes(r["uid"], live, among)]
    for pk in pruned_pks:
        delete_message(c, pk)
    mark_verified(c, account, folder, live, when)
    return len(pruned_pks), len(rows) - len(pruned_pks)
