"""What the Assistant reads of the mail (its Mail port)."""
from mailmind.kernel import attention as verdict
from mailmind.kernel.snoozed import not_snoozed_sql


def _among(accounts, col):
    """A clause confining a query to `accounts`, and its parameters: nothing when none are named."""
    if not accounts:
        return "", []
    return " AND %s IN (%s) " % (col, ",".join("?" for _ in accounts)), list(accounts)


def brief_rows(c, accounts, n):
    """What a brief is made from: the mail the AI said is worth a glance, most important first."""
    sql = ("SELECT m.pk, m.message_key, m.uid, m.account, m.folder, m.from_name, m.from_addr, m.subject, m.message_id, "
           "s.summary, s.importance, s.category, s.action "
           "FROM messages m JOIN assessments s ON s.message_key=m.message_key "
           f"WHERE m.is_junk=0 AND {not_snoozed_sql()} AND m.pending_removal IS NULL AND s.importance>=2 "
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
           "FROM messages m LEFT JOIN assessments s ON s.message_key=m.message_key "
           # a delta is "what changed" — include newsletters/notifications (the AI buckets them as noise)
           # so it matches the pill count; only drop spam/blocked and snoozed/muted.
           "WHERE (m.bucket IS NULL OR m.bucket NOT IN ('spam/scam','blocked')) "
           f"AND {not_snoozed_sql()} AND m.pending_removal IS NULL AND m.ts > ? "
           "AND NOT EXISTS (SELECT 1 FROM muted mu WHERE mu.account=m.account AND mu.thread_id=m.thread_id) ")
    params = [since_ts]
    if accounts:
        sql += "AND m.account IN (%s) " % ",".join("?" for _ in accounts)
        params += list(accounts)
    sql += "ORDER BY m.ts DESC LIMIT ?"       # newest first
    params.append(n)
    return c.execute(sql, params).fetchall()


def handled_since(c, accounts, since_iso):
    """How many messages a person sent to Archive since then, by the command log, which keeps each for a month. One an Undo took
    back doesn't count (command_mark_reversed), and nor does what Tidy filed on its own."""
    ac, ap = _among(accounts, "account")
    return c.execute("SELECT COUNT(*) FROM mail_commands WHERE kind='archive' AND applied=1 AND done_at>=? "
                     "AND json_extract(params, '$.undone') IS NULL "
                     "AND idempotency_key NOT GLOB '*:tidy-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'" + ac,
                     [since_iso, *ap]).fetchone()[0]


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


def sender_recent(c, addr, n):
    """A sender's newest mail, one row a message however many folders hold it, with what the AI said of each and nothing on
    its way out. For the Assistant, which is told the subjects and that, and no body."""
    return c.execute(
        "SELECT m.account, m.thread_id, m.from_name, m.subject, m.date_iso, m.ts, m.seen, m.answered, m.snippet, s.summary, s.category "
        "FROM messages m LEFT JOIN assessments s ON s.message_key=m.message_key "
        "WHERE lower(m.from_addr)=? AND m.pending_removal IS NULL "
        "GROUP BY m.message_key ORDER BY m.ts DESC LIMIT ?", (addr, n)).fetchall()


def sender_stats(c, addrs):
    """How each of these senders stands in the stored mail, one row a sender and each message counted once however many
    folders hold it. For the Assistant, which is told these numbers."""
    ph = ",".join("?" for _ in addrs)
    return c.execute(
        f"""SELECT addr, MAX(name) name, COUNT(*) n, SUM(seen=0) unseen, SUM(answered) replied, MIN(ts) first_ts, MAX(ts) last_ts
            FROM (SELECT lower(from_addr) addr, from_name name, MAX(seen) seen, MAX(COALESCE(answered, 0)) answered, MAX(ts) ts
                  FROM messages WHERE lower(from_addr) IN ({ph}) AND pending_removal IS NULL
                  GROUP BY lower(from_addr), COALESCE(message_key, -pk))
            GROUP BY addr ORDER BY n DESC""", addrs).fetchall()


def needs_you_rows(c, accounts, n):
    """Who's waiting on the person to reply: open, important, unanswered INBOX mail (mirrors the brief's "needs you"
    set; joins assessments so we get importance/action, excludes junk/snoozed/muted/answered)."""
    ac, ap = _among(accounts, "m.account")
    return c.execute(
        "SELECT m.pk, m.message_key, m.uid, m.folder, m.account, m.thread_id, m.from_name, m.from_addr, m.subject, m.ts, "
        "       s.importance, s.action, s.category "
        "FROM messages m JOIN assessments s ON s.message_key=m.message_key "
        f"WHERE m.folder='INBOX' AND m.is_junk=0 AND {not_snoozed_sql()} AND m.answered=0 "
        "  AND m.pending_removal IS NULL "
        f"  AND {verdict.needs_you_sql()} "
        "  AND NOT EXISTS (SELECT 1 FROM muted mu WHERE mu.account=m.account AND mu.thread_id=m.thread_id) "
        + ac + " ORDER BY s.importance DESC, m.ts DESC LIMIT ?", [*ap, n]).fetchall()


_DRAFT_CAND_SQL = """
SELECT m.pk, m.account, m.folder, m.thread_id, m.message_id, m.from_addr,
       m.is_junk, m.answered, NULL AS snooze_until, m.ts, s.importance, s.category
FROM messages m
JOIN assessments s ON s.message_key=m.message_key
WHERE m.folder = 'INBOX'
  AND m.is_junk = 0 AND m.answered = 0 AND m.pending_removal IS NULL
  AND """ + not_snoozed_sql() + """
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
