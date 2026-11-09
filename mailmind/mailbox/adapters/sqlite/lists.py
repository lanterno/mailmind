"""What the page lists: none holds a row on its way out (`pending_removal`). The digest and the store's counts are here too."""
from mailmind.kernel.conversation import count_sql
from mailmind.kernel.snoozed import not_snoozed_sql


def digest(c, limit=300):
    return c.execute(f"""SELECT m.*, s.summary, s.importance, s.category, s.action
           FROM messages m JOIN assessments s ON s.message_key=m.message_key
           WHERE m.is_junk = 0 AND {not_snoozed_sql()} AND m.pending_removal IS NULL AND s.importance >= 1
             AND NOT EXISTS (SELECT 1 FROM muted mu WHERE mu.account=m.account AND mu.thread_id=m.thread_id)
           ORDER BY s.importance DESC, m.ts DESC LIMIT ?""", (limit,)).fetchall()


def stored_accounts(c):
    """Each account that has mail stored."""
    return [row[0] for row in c.execute("SELECT DISTINCT account FROM messages ORDER BY account").fetchall()]


def stats(c):
    return {
        "total": c.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
        "kept": c.execute("SELECT COUNT(*) FROM messages WHERE is_junk=0").fetchone()[0],
        "summarized": c.execute("SELECT COUNT(*) FROM messages m JOIN assessments s ON s.message_key=m.message_key").fetchone()[0],
        "verified": c.execute("SELECT COUNT(*) FROM messages WHERE verified=1").fetchone()[0],
        "needs": c.execute(
            """SELECT COUNT(*) FROM messages m LEFT JOIN assessments s ON s.message_key=m.message_key
               WHERE m.is_junk=0 AND s.message_key IS NULL""").fetchone()[0],
    }


_MUTED_EXCL = ("AND NOT EXISTS (SELECT 1 FROM muted mu WHERE mu.account={t}.account "
               "AND mu.thread_id={t}.thread_id)")   # mirror the snoozed exclusion


def _of_accounts(accounts):
    """SQL and params for: `m` is of this one account, or of one of this list of them. An empty list matches nothing."""
    named = [accounts] if isinstance(accounts, str) else list(accounts)
    return f"m.account IN ({','.join('?' for _ in named)})", named


def snoozed_rows(c, accounts, limit, offset):
    """Every snoozed message in the account, or in each of a list of them, soonest wake first."""
    among, named = _of_accounts(accounts)
    return c.execute(
        f"""SELECT m.pk, m.message_key, m.uid, m.account, m.folder, m.from_addr, m.from_name, m.subject, m.date_iso, m.snippet,
                  m.seen, m.flagged, m.has_attach, m.is_junk, m.bucket, m.thread_id,
                  z.until AS snoozed_until, s.importance, s.category, s.action
           FROM snoozes z CROSS JOIN messages m ON m.message_key=z.message_key AND m.folder=z.folder
           LEFT JOIN assessments s ON s.message_key=m.message_key
           WHERE {among} AND m.pending_removal IS NULL
           ORDER BY z.until ASC, m.folder, m.ts, m.pk LIMIT ? OFFSET ?""", (*named, limit, offset)).fetchall()


def snoozed_count(c, accounts):
    among, named = _of_accounts(accounts)
    return c.execute("SELECT COUNT(*) FROM snoozes z CROSS JOIN messages m ON m.message_key=z.message_key AND m.folder=z.folder "
                     f"WHERE {among} AND m.pending_removal IS NULL", named).fetchone()[0]


def _listed(where):
    """What a list shows of the rows `where` picks, on `m`: none on its way out, snoozed or muted."""
    return f"{where} AND m.pending_removal IS NULL AND {not_snoozed_sql()} " + _MUTED_EXCL.format(t="m")


def _conversation_rows(c, where, params, partition, limit, offset):
    """One row per conversation (its latest message) + rollups, newest first. `partition` is what names a conversation here, and
    `cnt` is how many of it the list holds. `thread_count` is all of it, as opening it shows, asked of this page's rows only."""
    return c.execute(
        f"""WITH fm AS (
             SELECT m.*, s.importance, s.summary, s.action, s.category,
               ROW_NUMBER() OVER (PARTITION BY {partition} ORDER BY m.ts DESC, m.pk DESC) rn,
               COUNT(*)        OVER (PARTITION BY {partition}) cnt,
               SUM(CASE WHEN m.seen=0 THEN 1 ELSE 0 END) OVER (PARTITION BY {partition}) unread,
               MAX(m.flagged)  OVER (PARTITION BY {partition}) any_flagged,
               MAX(m.has_attach) OVER (PARTITION BY {partition}) any_attach
             FROM messages m LEFT JOIN assessments s ON s.message_key=m.message_key
             WHERE {_listed(where)}),
           page AS (SELECT * FROM fm WHERE rn=1 ORDER BY ts DESC, pk DESC LIMIT ? OFFSET ?)
           SELECT page.*, {count_sql("page")} AS thread_count FROM page ORDER BY ts DESC, pk DESC""",
        (*params, limit, offset)).fetchall()


def _message_rows(c, where, params, limit, offset):
    return c.execute(
        f"""SELECT m.pk, m.message_key, m.uid, m.account, m.folder, m.from_addr, m.from_name, m.subject, m.date_iso, m.ts, m.snippet,
                  m.seen, m.flagged, m.has_attach, m.is_junk, m.bucket, m.thread_id, s.importance,
                  s.summary, s.action, s.category
           FROM messages m LEFT JOIN assessments s ON s.message_key=m.message_key
           WHERE {_listed(where)}
           ORDER BY m.ts DESC, m.pk DESC LIMIT ? OFFSET ?""",
        (*params, limit, offset)).fetchall()


def _message_count(c, where, params):
    return c.execute(f"SELECT COUNT(*) FROM messages m WHERE {_listed(where)}", params).fetchone()[0]


_IN_FOLDER = "m.account=? AND m.folder=?"


def _in_inboxes(accounts):
    return "m.folder='INBOX' AND m.account IN (%s)" % ",".join("?" for _ in accounts)


def folder_conversation_rows(c, account, folder, limit, offset):
    """A folder collapsed to conversations."""
    return _conversation_rows(c, _IN_FOLDER, (account, folder), "m.thread_id", limit, offset)


def folder_conversation_count(c, account, folder):
    return c.execute(f"SELECT COUNT(DISTINCT m.thread_id) FROM messages m WHERE {_listed(_IN_FOLDER)}",
                     (account, folder)).fetchone()[0]


def folder_rows(c, account, folder, limit, offset):
    """A folder's messages, newest first."""
    return _message_rows(c, _IN_FOLDER, (account, folder), limit, offset)


def folder_count(c, account, folder):
    return _message_count(c, _IN_FOLDER, (account, folder))


def inboxes_conversation_rows(c, accounts, limit, offset):
    """Those accounts' Inboxes collapsed to conversations, as one list. A thread id means something within its account."""
    return _conversation_rows(c, _in_inboxes(accounts), tuple(accounts), "m.account, m.thread_id", limit, offset)


def inboxes_conversation_count(c, accounts):
    return c.execute(f"SELECT COUNT(*) FROM (SELECT DISTINCT m.account, m.thread_id FROM messages m "
                     f"WHERE {_listed(_in_inboxes(accounts))})", tuple(accounts)).fetchone()[0]


def inboxes_rows(c, accounts, limit, offset):
    """Those accounts' Inboxes as one list, newest first."""
    return _message_rows(c, _in_inboxes(accounts), tuple(accounts), limit, offset)


def inboxes_count(c, accounts):
    return _message_count(c, _in_inboxes(accounts), tuple(accounts))


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
        """SELECT pk, message_key, uid, folder, from_addr, from_name, subject, date_iso, ts, snippet,
                  seen, flagged, answered, has_attach, is_junk, message_id
           FROM messages WHERE account=? AND thread_id=? AND pending_removal IS NULL
           ORDER BY ts ASC, pk ASC""",
        (account, thread_id)).fetchall()
