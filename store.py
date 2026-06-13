"""SQLite store for MailMind: messages + AI summaries + per-account sync state."""
import sqlite3
import os

DB = os.path.join(os.path.dirname(__file__), "mailmind.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  pk           INTEGER PRIMARY KEY AUTOINCREMENT,
  account      TEXT NOT NULL,
  kind         TEXT NOT NULL,          -- gmail | proton
  folder       TEXT NOT NULL,
  uid          INTEGER NOT NULL,
  message_id   TEXT,                   -- RFC822 Message-ID (for Gmail deep-link)
  gmail_thread TEXT,
  from_addr    TEXT, from_name TEXT,
  subject      TEXT,
  date_iso     TEXT, ts INTEGER,
  snippet      TEXT,                   -- plain-text body, truncated
  bucket       TEXT,
  confidence   TEXT,                   -- mailclass confidence: high | med | low
  reason       TEXT,                   -- mailclass one-line rationale
  is_junk      INTEGER DEFAULT 0,
  verified     INTEGER DEFAULT 0,      -- 1 once reconcile confirmed the UID is still live
  last_seen    TEXT,                   -- ISO of last reconcile that saw this UID live
  UNIQUE(account, folder, uid)
);
CREATE TABLE IF NOT EXISTS summaries (
  message_pk  INTEGER PRIMARY KEY REFERENCES messages(pk) ON DELETE CASCADE,
  summary     TEXT,
  importance  INTEGER,                 -- 1..5
  category    TEXT,
  action      TEXT,                    -- short action item / next step, or ""
  model       TEXT,
  created_at  TEXT
);
CREATE TABLE IF NOT EXISTS sync_state (
  account   TEXT PRIMARY KEY,
  last_sync TEXT,
  last_uid  INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(ts);
CREATE INDEX IF NOT EXISTS idx_msg_junk ON messages(is_junk);
"""


def conn():
    c = sqlite3.connect(DB, timeout=60)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=60000")
    return c


_MIGRATIONS = [
    "ALTER TABLE sync_state ADD COLUMN last_uid INTEGER DEFAULT 0",
    "ALTER TABLE messages ADD COLUMN confidence TEXT",
    "ALTER TABLE messages ADD COLUMN reason TEXT",
    "ALTER TABLE messages ADD COLUMN verified INTEGER DEFAULT 0",
    "ALTER TABLE messages ADD COLUMN last_seen TEXT",
]


def init():
    c = conn()
    c.executescript(SCHEMA)
    for stmt in _MIGRATIONS:  # idempotent column adds for pre-existing DBs
        try:
            c.execute(stmt)
        except sqlite3.OperationalError:
            pass
    c.commit()
    c.close()


def get_message(c, pk):
    return c.execute("SELECT * FROM messages WHERE pk=?", (pk,)).fetchone()


def delete_message(c, pk):
    c.execute("DELETE FROM summaries WHERE message_pk=?", (pk,))
    c.execute("DELETE FROM messages WHERE pk=?", (pk,))


def get_watermark(c, account):
    r = c.execute("SELECT last_uid FROM sync_state WHERE account=?", (account,)).fetchone()
    return (r["last_uid"] or 0) if r else 0


def set_watermark(c, account, last_uid, last_sync):
    c.execute(
        """INSERT INTO sync_state(account,last_sync,last_uid) VALUES(?,?,?)
           ON CONFLICT(account) DO UPDATE SET last_sync=excluded.last_sync,
             last_uid=MAX(sync_state.last_uid, excluded.last_uid)""",
        (account, last_sync, last_uid),
    )


def upsert_message(c, m):
    m = {"confidence": None, "reason": None, **m}  # tolerate callers that omit them
    cur = c.execute(
        """INSERT INTO messages
           (account,kind,folder,uid,message_id,gmail_thread,from_addr,from_name,
            subject,date_iso,ts,snippet,bucket,confidence,reason,is_junk)
           VALUES (:account,:kind,:folder,:uid,:message_id,:gmail_thread,:from_addr,
            :from_name,:subject,:date_iso,:ts,:snippet,:bucket,:confidence,:reason,:is_junk)
           ON CONFLICT(account,folder,uid) DO UPDATE SET
            subject=excluded.subject, snippet=excluded.snippet,
            bucket=excluded.bucket, confidence=excluded.confidence,
            reason=excluded.reason, is_junk=excluded.is_junk
           RETURNING pk""",
        m,
    )
    return cur.fetchone()[0]


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
    c.execute(f"UPDATE messages SET verified=0 WHERE account=? AND folder=?", (account, folder))
    if live:
        qs = ",".join("?" * len(live))
        c.execute(f"""UPDATE messages SET verified=1, last_seen=?
                      WHERE account=? AND folder=? AND uid IN ({qs})""",
                  [when, account, folder, *live])


def reconcile(c, account, folder, live_uids, when):
    """Prune INBOX rows whose UID is no longer live; stamp survivors verified.
    Returns (pruned, kept). Caller MUST guarantee live_uids came from a real,
    successful SEARCH (never call with an empty set on an errored fetch)."""
    live = set(int(u) for u in live_uids)
    rows = c.execute("SELECT pk, uid FROM messages WHERE account=? AND folder=?",
                     (account, folder)).fetchall()
    pruned_pks = [r["pk"] for r in rows if r["uid"] not in live]
    for pk in pruned_pks:
        delete_message(c, pk)
    mark_verified(c, account, folder, live, when)
    return len(pruned_pks), len(rows) - len(pruned_pks)


def needs_summary(c, limit=200):
    """Non-junk messages without a summary yet, newest first."""
    return c.execute(
        """SELECT m.* FROM messages m
           LEFT JOIN summaries s ON s.message_pk = m.pk
           WHERE m.is_junk = 0 AND s.message_pk IS NULL
           ORDER BY m.ts DESC LIMIT ?""", (limit,)
    ).fetchall()


def save_summary(c, pk, summary, importance, category, action, model, created_at):
    c.execute(
        """INSERT INTO summaries (message_pk,summary,importance,category,action,model,created_at)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(message_pk) DO UPDATE SET
            summary=excluded.summary, importance=excluded.importance,
            category=excluded.category, action=excluded.action,
            model=excluded.model, created_at=excluded.created_at""",
        (pk, summary, importance, category, action, model, created_at),
    )


def digest(c, min_importance=1, account=None, limit=300):
    q = """SELECT m.*, s.summary, s.importance, s.category, s.action
           FROM messages m JOIN summaries s ON s.message_pk = m.pk
           WHERE m.is_junk = 0 AND s.importance >= ?"""
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
