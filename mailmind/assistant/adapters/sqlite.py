"""What the AI said and what it was asked: summaries and assessments, the brief, the delta and the review,
thread summaries, replies drafted ahead and message batches. Each function takes its caller's connection."""
from datetime import datetime, timedelta, timezone

from mailmind.kernel import attention as verdict
from mailmind.kernel.assessment import Assessment


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


def rekey_thread(c, account, survivor, absorbed):
    """Thread `absorbed` merged into `survivor`: its summaries and the reply drafted ahead for it move, in the merge's
    transaction. Where `survivor` has its own, that one stays."""
    c.execute("UPDATE OR IGNORE thread_summaries SET thread_id=? WHERE account=? AND thread_id=?", (survivor, account, absorbed))
    c.execute("DELETE FROM thread_summaries WHERE account=? AND thread_id=?", (account, absorbed))
    c.execute("UPDATE OR IGNORE draft_ahead SET ref_key=? WHERE account=? AND ref_key=?",
              ("t:" + survivor, account, "t:" + absorbed))
    c.execute("DELETE FROM draft_ahead WHERE account=? AND ref_key=?", (account, "t:" + absorbed))


# ------------------------------------------------------------------- the brief, the delta, the review
def _forget_old_answers(c, table):
    """Each is read on the day it was written: a week keeps its table tiny."""
    c.execute(f"DELETE FROM {table} WHERE created_at < ?",
              ((datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),))


def get_brief(c, accounts, day):
    return c.execute("SELECT * FROM briefs WHERE accounts=? AND day=?", (accounts, day)).fetchone()


def save_brief(c, accounts, day, fingerprint, narrative, groups_json, model, created_at):
    c.execute("INSERT OR REPLACE INTO briefs(accounts, day, fingerprint, narrative, groups_json, model, created_at) "
              "VALUES(?,?,?,?,?,?,?)", (accounts, day, fingerprint, narrative, groups_json, model, created_at))
    _forget_old_answers(c, "briefs")


def get_delta(c, accounts, since_ts, fingerprint):
    return c.execute("SELECT * FROM deltas WHERE accounts=? AND since_ts=? AND fingerprint=?",
                     (accounts, since_ts, fingerprint)).fetchone()


def save_delta(c, accounts, since_ts, fingerprint, headline, items_json, model, created_at):
    c.execute("INSERT OR REPLACE INTO deltas(accounts, since_ts, fingerprint, headline, items_json, model, created_at) "
              "VALUES(?,?,?,?,?,?,?)", (accounts, since_ts, fingerprint, headline, items_json, model, created_at))
    _forget_old_answers(c, "deltas")


def get_review(c, accounts, day):
    return c.execute("SELECT * FROM reviews WHERE accounts=? AND day=?", (accounts, day)).fetchone()


def save_review(c, accounts, day, fingerprint, narrative, next_json, stats_json, needs_json, waiting_json,
                model, created_at):
    c.execute("INSERT OR REPLACE INTO reviews(accounts, day, fingerprint, narrative, next_json, stats_json, "
              "needs_json, waiting_json, model, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
              (accounts, day, fingerprint, narrative, next_json, stats_json, needs_json, waiting_json,
               model, created_at))
    _forget_old_answers(c, "reviews")


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


def reply_worthy(c, pk):
    """The kick gate: the background tick's bar too. Needed because the reader surface arms on any
    opened mail; gates KICKS only, never serving."""
    r = c.execute("SELECT 1 FROM messages m JOIN assessments s ON s.message_key=m.message_key "
                  f"WHERE m.pk=? AND {verdict.reply_worthy_sql()}", (pk,)).fetchone()
    return bool(r)


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


def needs_summary(c, limit=200, newer_than=None):
    """Non-junk messages without a summary yet, newest first: one copy of each, since what the AI says
    of it is kept under its key. A message a batch holds isn't one of them, nor a row with no key, which
    has nowhere to keep an answer until the next start gives it one (repair_keys). With `newer_than`,
    only what came after that moment: while a batch is out the rest of the backlog waits for the next,
    or it would go at full price."""
    return c.execute(
        """SELECT m.* FROM messages m
           LEFT JOIN assessments s ON s.message_key = m.message_key
           WHERE m.is_junk = 0 AND s.message_key IS NULL AND m.pending_removal IS NULL
             AND m.message_key IS NOT NULL
             AND COALESCE(m.message_key, 0) NOT IN (SELECT message_key FROM ai_batch_items)
             AND COALESCE(m.ts, 0) > ?
           GROUP BY COALESCE(m.message_key, -m.pk)
           ORDER BY m.ts DESC LIMIT ?""", (-1 if newer_than is None else newer_than, limit)
    ).fetchall()


_BATCH_OUT = "('submitting', 'running')"


def batch_open(c):
    """The batch that is out, if any: there is one at a time."""
    return c.execute(f"SELECT * FROM ai_batches WHERE state IN {_BATCH_OUT} ORDER BY id DESC LIMIT 1").fetchone()


def batch_begin(c, feature, model, keys, estimate_micros, cutoff_ts, now, prompt_version=None):
    """Record a batch about to be sent and hold its messages, so nothing triages them meanwhile: the caller commits
    this before it sends, which is a network call. None if another is out already."""
    cur = c.execute(
        "INSERT INTO ai_batches(feature, model, state, requests, estimate_micros, cutoff_ts, created_at, prompt_version) "
        f"SELECT ?, ?, 'submitting', ?, ?, ?, ?, ? WHERE NOT EXISTS(SELECT 1 FROM ai_batches WHERE state IN {_BATCH_OUT})",
        (feature, model, len(keys), estimate_micros, cutoff_ts, now, prompt_version))
    if cur.rowcount != 1:
        return None
    batch_id = cur.lastrowid
    c.executemany("INSERT INTO ai_batch_items(message_key, batch_id) VALUES (?,?)", [(k, batch_id) for k in keys])
    return batch_id


def batch_sent(c, batch_id, remote_id, now):
    c.execute("UPDATE ai_batches SET state='running', remote_id=?, polled_at=? WHERE id=?", (remote_id, now, batch_id))


def batch_drop(c, batch_id):
    """It never went, or the API no longer has it: its messages are waiting again."""
    c.execute("DELETE FROM ai_batches WHERE id=?", (batch_id,))


def batch_polled(c, batch_id, now):
    c.execute("UPDATE ai_batches SET polled_at=? WHERE id=?", (now, batch_id))


def batch_keys(c, batch_id):
    return {r[0] for r in c.execute("SELECT message_key FROM ai_batch_items WHERE batch_id=?", (batch_id,))}


def batch_collected(c, batch_id, now):
    """Its answers are stored. A message without one is waiting again; the API still holds a copy."""
    c.execute("DELETE FROM ai_batch_items WHERE batch_id=?", (batch_id,))
    c.execute("UPDATE ai_batches SET state='collected', ended_at=? WHERE id=?", (now, batch_id))


def batch_discarded(c, batch_id):
    c.execute("UPDATE ai_batches SET state='done' WHERE id=?", (batch_id,))


def batches_in_state(c, state):
    return c.execute("SELECT * FROM ai_batches WHERE state=? ORDER BY id", (state,)).fetchall()


def batch_waiting(c):
    """How many messages are out in a batch."""
    return c.execute("SELECT COUNT(*) FROM ai_batch_items").fetchone()[0]


def batch_forget_finished(c, now_ts, days=30):
    c.execute("DELETE FROM ai_batches WHERE state='done' AND ended_at < ?", (now_ts - days * 86400,))


def save_summary(c, pk, summary, importance, category, action, model, created_at, served_by=None, prompt_version=None,
                 outcome=None):
    """What the AI said of a message, for this copy, the others and any that comes later. How it came to say it is kept
    under the key alone, which outlives the copies."""
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
        save_assessment(c, key, *said, served_by, prompt_version, outcome)


def save_assessment(c, key, summary, importance, category, action, model, created_at, served_by=None, prompt_version=None,
                    outcome=None):
    """What the AI said of a message, under its key, for a copy that comes later."""
    c.execute(
        """INSERT INTO assessments (message_key,summary,importance,category,action,model,created_at,
                                    served_by,prompt_version,outcome)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(message_key) DO UPDATE SET
            summary=excluded.summary, importance=excluded.importance,
            category=excluded.category, action=excluded.action,
            model=excluded.model, created_at=excluded.created_at,
            served_by=excluded.served_by, prompt_version=excluded.prompt_version, outcome=excluded.outcome""",
        (key, summary, importance, category, action, model, created_at, served_by, prompt_version, outcome),
    )


def assessment(c, key):
    """What is kept of a message under its key, or None. What a row from before migration 43 never said stays unknown."""
    r = c.execute("""SELECT summary, importance, category, action, model, served_by, prompt_version, outcome
                     FROM assessments WHERE message_key=?""", (key,)).fetchone()
    if r is None:
        return None
    return Assessment(r["summary"] or "", r["importance"], r["category"] or "", r["action"] or "", r["model"] or "",
                      r["served_by"], r["prompt_version"], r["outcome"])
