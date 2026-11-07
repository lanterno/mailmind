"""Attention's tables: `snoozes`, `muted` and `followups`, and `messages.snoozed_until`, which only a rollback would read.
Each function takes its caller's connection and leaves the commit to it."""
from mailmind.attention.domain import followups
from mailmind.kernel import attention
from mailmind.kernel.snoozed import not_snoozed_sql
from mailmind.kernel.when import now_iso


def thread_pks(c, account, thread_id):
    return [r["pk"] for r in c.execute(
        "SELECT pk FROM messages WHERE account=? AND thread_id=? AND pending_removal IS NULL",
        (account, thread_id))]


def _write_column(c, pk, until):
    """`messages.snoozed_until`, written for a rollback to an older commit: on the row, and on any other copy in its
    folder, since `snoozes` hides them all. Nothing here reads it."""
    c.execute("UPDATE messages SET snoozed_until=? WHERE pk=?", (until, pk))
    c.execute("""UPDATE messages SET snoozed_until=? WHERE (message_key, folder) IN
                 (SELECT message_key, folder FROM messages WHERE pk=?)""", (until, pk))


def set_snooze(c, pk, until):
    """Hide message `pk` until `until`. It's kept under the message's key and folder, so it outlives
    the row: a copy that arrives in that folder is hidden too."""
    _write_column(c, pk, int(until))
    c.execute("""INSERT OR REPLACE INTO snoozes(message_key, folder, until)
                 SELECT message_key, folder, ? FROM messages WHERE pk=? AND message_key IS NOT NULL""",
              (int(until), pk))


def clear_snooze(c, pk):
    c.execute("""DELETE FROM snoozes WHERE (message_key, folder) IN
                 (SELECT message_key, folder FROM messages WHERE pk=?)""", (pk,))
    _write_column(c, pk, None)


def due_snoozes(c, now_ts):
    """The rows to wake. A snooze come due with no copy left in its folder ends here: the message
    was filed, and bringing it back later isn't waking it."""
    c.execute("""DELETE FROM snoozes WHERE until<=? AND NOT EXISTS
                 (SELECT 1 FROM messages m WHERE m.message_key=snoozes.message_key AND m.folder=snoozes.folder)""",
              (now_ts,))
    return c.execute("SELECT m.* FROM snoozes z CROSS JOIN messages m ON m.message_key=z.message_key AND m.folder=z.folder "
                     "WHERE z.until<=?", (now_ts,)).fetchall()


# ----------------------------------------------------------- mute (silence a thread)
def rekey_thread(c, account, survivor, absorbed):
    """Thread `absorbed` merged into `survivor`: its mute and its follow-up move, in the merge's transaction. Where
    `survivor` has its own, that one stays."""
    c.execute("UPDATE OR IGNORE muted SET thread_id=? WHERE account=? AND thread_id=?", (survivor, account, absorbed))
    c.execute("DELETE FROM muted WHERE account=? AND thread_id=?", (account, absorbed))
    c.execute("UPDATE OR IGNORE followups SET thread_id=? WHERE account=? AND thread_id=?", (survivor, account, absorbed))
    c.execute("DELETE FROM followups WHERE account=? AND thread_id=?", (account, absorbed))


def mute_thread(c, account, thread_id, when):
    c.execute("INSERT OR IGNORE INTO muted(account, thread_id, muted_at) VALUES(?,?,?)",
              (account, thread_id, when))


def unmute_thread(c, account, thread_id):
    c.execute("DELETE FROM muted WHERE account=? AND thread_id=?", (account, thread_id))


def is_muted(c, account, thread_id):
    return bool(c.execute("SELECT 1 FROM muted WHERE account=? AND thread_id=?",
                          (account, thread_id)).fetchone())


def unread_in_thread(c, account, thread_id):
    return c.execute("SELECT * FROM messages WHERE account=? AND thread_id=? AND seen=0",
                     (account, thread_id)).fetchall()


def a_member(c, account, thread_id):
    """One message of the thread, whichever: what can say later which thread this has become."""
    return c.execute("SELECT message_key, folder FROM messages WHERE account=? AND thread_id=? AND message_key IS NOT NULL LIMIT 1",
                     (account, thread_id)).fetchone()


def unread_in_muted_threads(c, account):
    """Unseen members of any muted thread. One whose mark is still open, or was refused and waits in the tray, isn't
    asked for again each sync."""
    return c.execute("""SELECT * FROM messages m WHERE m.account=? AND m.seen=0 AND m.thread_id IN
                          (SELECT thread_id FROM muted WHERE account=?)
                        AND NOT EXISTS (SELECT 1 FROM mail_commands q WHERE q.account=m.account AND q.kind='flag'
                          AND q.applied=0 AND q.message_id=m.message_id AND q.message_id!='')""",
                     (account, account)).fetchall()


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


# How each condition of domain.followups's rules reads in SQL. A statement is built from the domain's names, so a condition
# the domain adds or drops is one this file has to say or goes unasked.
_MUTED = "EXISTS (SELECT 1 FROM muted mu WHERE mu.account=followups.account AND mu.thread_id=followups.thread_id)"

_COMES_DUE = {
    "sent_and_pending": "kind='sent' AND state='pending'",
    "past_the_grace": "first_sent_ts <= :cutoff",
    "no_answer_since": """NOT EXISTS (SELECT 1 FROM messages m WHERE m.account=followups.account
                       AND m.thread_id=followups.thread_id AND m.ts > followups.first_sent_ts
                       AND lower(m.from_addr) <> lower(followups.account))""",
    "not_muted": "NOT " + _MUTED,
}
_PROMOTE = ("UPDATE followups SET state='due', due_ts=:now WHERE "
            + " AND ".join(_COMES_DUE[name] for name in followups.COMES_DUE))

_RECEIVED_NUDGE = {
    "in_a_thread": "m.thread_id IS NOT NULL AND m.thread_id != ''",
    "read": "m.seen=1",
    "unanswered": "m.answered=0",
    "not_junk": "m.is_junk=0",
    "past_the_grace": "m.ts <= :cutoff",
    "within_its_lifetime": "m.ts >= :floor",
    "the_threads_newest": """m.pk=(SELECT pk FROM messages m2 WHERE m2.account=m.account AND m2.thread_id=m.thread_id
                      AND m2.draft=0 ORDER BY m2.ts DESC, m2.pk DESC LIMIT 1)""",
    "not_snoozed": not_snoozed_sql(),
    "worth_a_reply": """EXISTS (SELECT 1 FROM messages mi JOIN assessments s ON s.message_key=mi.message_key
                WHERE mi.account=m.account AND mi.thread_id=m.thread_id
                  AND """ + attention.reply_worthy_sql() + ")",
    "nothing_from_me": """NOT EXISTS (SELECT 1 FROM messages mo WHERE mo.account=m.account AND mo.draft=0
                AND mo.thread_id=m.thread_id AND lower(mo.from_addr)=lower(m.account))""",
    "not_muted": "NOT EXISTS (SELECT 1 FROM muted mu WHERE mu.account=m.account AND mu.thread_id=m.thread_id)",
    "no_follow_up_yet": "NOT EXISTS (SELECT 1 FROM followups f WHERE f.account=m.account AND f.thread_id=m.thread_id)",
}
_DETECT = ("""INSERT OR IGNORE INTO followups(account,thread_id,kind,state,anchor_pk,last_msg_ts,due_ts,created_at)
        SELECT m.account, m.thread_id, 'received', 'due', m.pk, m.ts, :now, :when
        FROM messages m
        WHERE """ + " AND ".join(_RECEIVED_NUDGE[name] for name in followups.RECEIVED_NUDGE))

_MET = {
    "muted": _MUTED,
    "they_answered": """(followups.kind='sent' AND EXISTS (SELECT 1 FROM messages m WHERE m.account=followups.account
              AND m.thread_id=followups.thread_id AND m.ts > IFNULL(followups.first_sent_ts,0)
              AND lower(m.from_addr) <> lower(followups.account)))""",
    "i_replied": """(followups.kind IN ('received','replylater') AND EXISTS (SELECT 1 FROM messages m WHERE m.account=followups.account
              AND m.thread_id=followups.thread_id AND (m.answered=1 OR
                  (lower(m.from_addr)=lower(followups.account) AND m.draft=0))))""",
}
_RESOLVE = ("DELETE FROM followups WHERE account=? AND state IN ('pending','due') AND ("
            + " OR ".join(_MET[name] for name in followups.MET) + ")")

_EXPIRES = {
    "received": "kind='received'",
    "live": "state IN ('pending','due')",
    "past_its_lifetime": "last_msg_ts IS NOT NULL AND last_msg_ts < :floor",
}
_EXPIRE = "DELETE FROM followups WHERE " + " AND ".join(_EXPIRES[name] for name in followups.EXPIRES)


def followup_promote_due(c, now_ts, days):
    """Sent-no-reply rows that domain.followups.COMES_DUE says have waited long enough become due."""
    c.execute(_PROMOTE, {"now": now_ts, "cutoff": followups.days_before(now_ts, days)})


def followup_detect_received(c, now_ts, days, max_age_days):
    """Threads domain.followups.RECEIVED_NUDGE says want a nudge, within `days` and `max_age_days` of now, get one."""
    c.execute(_DETECT, {"now": now_ts, "when": now_iso(), "cutoff": followups.days_before(now_ts, days),
                        "floor": followups.days_before(now_ts, max_age_days)})


def resolve_followups(c, account):
    """Delete the account's live follow-ups whose commitment domain.followups.MET says is met."""
    c.execute(_RESOLVE, (account,))


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


def waiting_on_others(c, accounts, limit):
    """A row like list_waiting's per sent thread still with no reply, in those accounts, the longest wait first."""
    return c.execute(
        """SELECT m.*, f.kind AS f_kind, f.first_sent_ts AS f_first_sent, f.due_ts AS f_due,
                  (SELECT COUNT(*) FROM messages mm WHERE mm.account=m.account AND mm.thread_id=m.thread_id) AS thread_count
           FROM followups f JOIN messages m ON m.account=f.account AND m.thread_id=f.thread_id
           WHERE f.account IN (%s) AND f.state='due' AND f.kind='sent'
             AND m.pk=(SELECT pk FROM messages m2 WHERE m2.account=m.account AND m2.thread_id=m.thread_id
                       ORDER BY m2.ts DESC, m2.pk DESC LIMIT 1)
           ORDER BY m.ts, m.pk LIMIT ?""" % ",".join("?" for _ in accounts), (*accounts, limit)).fetchall()


def waiting_count(c, account):
    return c.execute(
        "SELECT COUNT(DISTINCT f.thread_id) FROM followups f "
        "JOIN messages m ON m.account=f.account AND m.thread_id=f.thread_id "
        "WHERE f.account=? AND f.state='due' AND f.kind!='replylater'", (account,)).fetchone()[0]


def _among(accounts):
    """A clause confining a query of `followups f` to `accounts`, and its parameters: nothing when none are named."""
    if not accounts:
        return "", []
    return " AND f.account IN (%s) " % ",".join("?" for _ in accounts), list(accounts)


def awaited_threads(c, accounts, n):
    """Who the person is waiting on, for a review: SENT-no-reply follow-ups only (kind='sent'; you replied, no answer
    yet). kind='received' is the inverse (they're waiting on you). Subject and recipients come from the thread's LATEST
    message on the STABLE (account,thread_id) key: anchor_pk is advisory and doesn't survive a reconcile/reset_folder
    re-pk (would blank the row)."""
    fac, fap = _among(accounts)
    return c.execute(
        "SELECT f.account, f.thread_id, m.pk, m.message_key, m.folder, f.first_sent_ts, m.subject, m.recipients "
        "FROM followups f LEFT JOIN messages m ON m.account=f.account AND m.thread_id=f.thread_id "
        "  AND m.pk=(SELECT pk FROM messages m2 WHERE m2.account=f.account AND m2.thread_id=f.thread_id "
        "            ORDER BY m2.ts DESC, m2.pk DESC LIMIT 1) "
        "WHERE f.state='due' AND f.kind='sent'" + fac
        + " ORDER BY f.first_sent_ts ASC LIMIT ?", [*fap, n]).fetchall()


def replylater_due(c, accounts):
    fac, fap = _among(accounts)
    return c.execute(
        "SELECT COUNT(*) FROM followups f WHERE f.state='due' AND f.kind='replylater'" + fac,
        fap).fetchone()[0]


def expire_followups(c, now_ts, days):
    """Delete the `received` nudges domain.followups.EXPIRES says have nagged long enough."""
    c.execute(_EXPIRE, {"floor": followups.days_before(now_ts, days)})


def due_followup_keys(c):
    """The set of currently-due (account, thread_id) pairs — to detect a swap (count unchanged)."""
    return {(r["account"], r["thread_id"]) for r in
            c.execute("SELECT account, thread_id FROM followups WHERE state='due'").fetchall()}
