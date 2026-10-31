"""SQLite store for MailMind: messages + AI summaries + per-account sync state."""
import sqlite3
import os
from datetime import datetime, timezone, timedelta

DB = os.path.join(os.path.dirname(__file__), "mailmind.db")

# Latest schema (fresh DBs get this directly; older DBs are upgraded by the
# versioned migrator below). All tables use CREATE IF NOT EXISTS so executescript
# is safe on an existing DB; the migrator handles in-place changes (e.g. the
# sync_state primary-key change) that IF NOT EXISTS can't.
SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  pk           INTEGER PRIMARY KEY AUTOINCREMENT,
  account      TEXT NOT NULL,
  kind         TEXT NOT NULL,          -- gmail | proton | imap
  folder       TEXT NOT NULL,
  uid          INTEGER NOT NULL,
  message_id   TEXT,                   -- RFC822 Message-ID
  gmail_thread TEXT,                   -- X-GM-THRID (gmail) for threading
  thread_id    TEXT,                   -- cross-provider computed thread id
  in_reply_to  TEXT, refs TEXT,        -- In-Reply-To / References for threading
  from_addr    TEXT, from_name TEXT,
  subject      TEXT,
  date_iso     TEXT, ts INTEGER,
  snippet      TEXT,                   -- plain-text body, truncated (list + AI)
  bucket       TEXT,
  confidence   TEXT,
  reason       TEXT,
  is_junk      INTEGER DEFAULT 0,      -- a BADGE now (Mailbox shows junk too)
  verified     INTEGER DEFAULT 0,
  last_seen    TEXT,
  seen         INTEGER DEFAULT 0,      -- \\Seen
  flagged      INTEGER DEFAULT 0,      -- \\Flagged (star)
  answered     INTEGER DEFAULT 0,      -- \\Answered
  draft        INTEGER DEFAULT 0,      -- \\Draft
  deleted      INTEGER DEFAULT 0,      -- \\Deleted
  has_attach   INTEGER DEFAULT 0,
  body_fetched INTEGER DEFAULT 0,      -- 1 once raw RFC822 is in bodies
  body_text    TEXT,                   -- plain-text body for FTS (NULL until fetched)
  snoozed_until INTEGER,               -- unix secs; NULL = not snoozed (hidden from lists until due)
  UNIQUE(account, folder, uid)
);
CREATE TABLE IF NOT EXISTS summaries (
  message_pk  INTEGER PRIMARY KEY REFERENCES messages(pk) ON DELETE CASCADE,
  summary     TEXT, importance INTEGER, category TEXT, action TEXT, model TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS sync_state (
  account     TEXT NOT NULL,
  folder      TEXT NOT NULL DEFAULT 'INBOX',
  last_sync   TEXT,
  last_uid    INTEGER DEFAULT 0,
  uidvalidity INTEGER,
  last_ok_ts  TEXT,                    -- health: last successful sync (added _m15)
  last_error  TEXT,                    -- exception CLASS name only (scrubbed; never the message)
  last_error_ts TEXT,
  last_error_kind TEXT,                -- coarse enum: TIMEOUT|AUTH|UIDVALIDITY|IMAP|NETWORK|UNKNOWN
  sync_count  INTEGER DEFAULT 0,
  PRIMARY KEY(account, folder)
);
CREATE TABLE IF NOT EXISTS bodies (
  message_pk  INTEGER PRIMARY KEY REFERENCES messages(pk) ON DELETE CASCADE,
  raw         BLOB,                    -- raw RFC822 bytes (lossless; parse on read)
  fetched_at  TEXT
);
CREATE TABLE IF NOT EXISTS attachments (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  message_pk  INTEGER REFERENCES messages(pk) ON DELETE CASCADE,
  part_id     TEXT, filename TEXT, content_type TEXT, size INTEGER,
  sha256      TEXT,                    -- content-addressed file under attachments/
  is_inline   INTEGER DEFAULT 0, content_id TEXT
);
CREATE TABLE IF NOT EXISTS outbox (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  account         TEXT NOT NULL,          -- the FROM account email (must exist in config)
  idempotency_key TEXT NOT NULL UNIQUE,   -- one logical send per key (dedupes double-submit)
  message_id      TEXT NOT NULL,          -- minted at build; stable across retries
  in_reply_to_pk  INTEGER,                -- original pk (reply) -> set \\Answered after send
  envelope        TEXT NOT NULL,          -- JSON [addr-spec,...] full delivery set incl. Bcc (PRIVATE; never in raw)
  recipients      TEXT NOT NULL,          -- JSON {to,cc,bcc} for the outbox UI only
  subject         TEXT,
  raw             BLOB NOT NULL,          -- Bcc-LESS RFC822 bytes: sent on the wire AND APPENDed to Sent
  state           TEXT NOT NULL DEFAULT 'queued',   -- queued | sending | sent | error
  attempts        INTEGER NOT NULL DEFAULT 0,
  next_attempt_at INTEGER NOT NULL DEFAULT 0,        -- unix secs; backoff gate
  delivered       INTEGER NOT NULL DEFAULT 0,        -- 1 once the wire send succeeded (never re-send)
  sent_appended   INTEGER NOT NULL DEFAULT 0,        -- done-once: Sent copy made
  answered_set    INTEGER NOT NULL DEFAULT 0,        -- done-once: \\Answered set on original
  followup_recorded INTEGER NOT NULL DEFAULT 0,      -- done-once: sent-no-reply followup seeded
  last_error      TEXT,                              -- SCRUBBED code only
  draft_id        INTEGER,                           -- supersede this draft after delivery
  draft_deleted   INTEGER NOT NULL DEFAULT 0,        -- done-once: draft removed post-send
  created_at      TEXT NOT NULL,
  sent_at         TEXT
);
CREATE TABLE IF NOT EXISTS read_mutations (   -- offline-safe read-side mutation queue (mirrors outbox); keyed on message_id, NEVER pk
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  account         TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,        -- one logical mutation per key (dedupes double-submit)
  op              TEXT NOT NULL,               -- trash | archive | junk | move | flag
  message_id      TEXT,                        -- RFC822 Message-ID: the DURABLE key (survives reconcile re-pk)
  src_folder      TEXT NOT NULL,               -- folder the message lived in at enqueue
  dest            TEXT,                        -- move/junk/archive target (NULL for flag / gmail label-remove)
  payload         TEXT NOT NULL,               -- JSON {pk,uid,kind,flags?,is_gmail,undo_label,subject,thread_id}
  state           TEXT NOT NULL DEFAULT 'queued',  -- queued | running | done | error
  attempts        INTEGER NOT NULL DEFAULT 0,
  next_attempt_at INTEGER NOT NULL DEFAULT 0,
  applied         INTEGER NOT NULL DEFAULT 0,  -- EXACTLY-ONCE flag (mirrors outbox.delivered): set the instant the IMAP op lands
  last_error      TEXT,                        -- SCRUBBED code only
  created_at      TEXT NOT NULL,
  done_at         TEXT
);
CREATE TABLE IF NOT EXISTS thread_summaries (
  account     TEXT NOT NULL,
  thread_id   TEXT NOT NULL,
  scope       TEXT NOT NULL DEFAULT 'thread',  -- thread | message
  fingerprint TEXT NOT NULL,                   -- content hash; mismatch => stale
  tldr        TEXT, points TEXT, action TEXT, participants TEXT,  -- points/participants = JSON
  message_count INTEGER, model TEXT, created_at TEXT,
  PRIMARY KEY(account, thread_id, scope)
);
CREATE TABLE IF NOT EXISTS drafts (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  account     TEXT NOT NULL, folder TEXT, uid INTEGER, message_id TEXT, uidvalidity INTEGER,
  in_reply_to_pk INTEGER, mode TEXT, subject TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS briefing_cache (   -- AI "Today" card, keyed on (sorted-accounts|local-date)
  key TEXT PRIMARY KEY, fingerprint TEXT, narrative TEXT, groups_json TEXT, model TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS muted (            -- silenced threads; keyed on (account,thread_id), NEVER pk
  account TEXT NOT NULL, thread_id TEXT NOT NULL, muted_at TEXT, PRIMARY KEY(account, thread_id)
);
CREATE INDEX IF NOT EXISTS idx_muted_acct ON muted(account);
CREATE TABLE IF NOT EXISTS followups (        -- commitment tracking; keyed on (account,thread_id), NEVER pk
  account TEXT NOT NULL, thread_id TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
  anchor_pk INTEGER, first_sent_ts INTEGER, last_msg_ts INTEGER, due_ts INTEGER, created_at TEXT, resolved_at TEXT,
  PRIMARY KEY(account, thread_id)
);
CREATE INDEX IF NOT EXISTS idx_followups_state ON followups(account, state);
CREATE TABLE IF NOT EXISTS draft_ahead (     -- durable AI pre-draft cache; key (account,ref_key) NEVER pk
  account        TEXT NOT NULL,
  ref_key        TEXT NOT NULL,              -- 't:<thread_id>' or 'm:<message_id>'
  fp             TEXT NOT NULL DEFAULT '',   -- thread-state hash captured BEFORE generation
  body           TEXT,                       -- NULL => failure-only row, never servable
  tone           TEXT, model TEXT,
  source         TEXT NOT NULL DEFAULT 'dwell',  -- 'dwell' | 'topk' | 'demand'
  src_pk         INTEGER,                    -- advisory only: which message the draft answers
  src_message_id TEXT,
  created_ts     INTEGER NOT NULL DEFAULT 0,
  last_hit_ts    INTEGER,
  fail_ts        INTEGER NOT NULL DEFAULT 0, fail_count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(account, ref_key)
);
CREATE INDEX IF NOT EXISTS idx_draft_ahead_created ON draft_ahead(created_ts);
CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(ts);
CREATE INDEX IF NOT EXISTS idx_msg_junk ON messages(is_junk);
CREATE INDEX IF NOT EXISTS idx_att_msg ON attachments(message_pk);
CREATE INDEX IF NOT EXISTS idx_outbox_state ON outbox(state, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_readmut_state ON read_mutations(state, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_outbox_created ON outbox(created_at);
-- indexes on columns added by migrations (thread_id, account/folder) are created in _m3
"""


def conn():
    c = sqlite3.connect(DB, timeout=60)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=60000")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def _has_col(c, table, col):
    return col in [r[1] for r in c.execute(f"PRAGMA table_info({table})")]


def _addcol(c, table, coldef):
    try:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {coldef}")
    except sqlite3.OperationalError:
        pass  # already there


def _m1_sync_state_folder(c):
    """sync_state: PRIMARY KEY(account) -> (account,folder) + uidvalidity.
    Existing per-account watermarks ARE the INBOX watermark — map them there."""
    if _has_col(c, "sync_state", "folder"):
        return  # already the new shape (fresh DB or re-run)
    c.execute("ALTER TABLE sync_state RENAME TO sync_state_old")
    c.executescript("""CREATE TABLE sync_state (
        account TEXT NOT NULL, folder TEXT NOT NULL DEFAULT 'INBOX',
        last_sync TEXT, last_uid INTEGER DEFAULT 0, uidvalidity INTEGER,
        PRIMARY KEY(account, folder));""")
    c.execute("""INSERT INTO sync_state(account,folder,last_sync,last_uid)
                 SELECT account,'INBOX',last_sync,last_uid FROM sync_state_old""")
    c.execute("DROP TABLE sync_state_old")


def _m2_message_flags(c):
    for col in ("seen INTEGER DEFAULT 0", "flagged INTEGER DEFAULT 0",
                "answered INTEGER DEFAULT 0", "draft INTEGER DEFAULT 0",
                "deleted INTEGER DEFAULT 0", "has_attach INTEGER DEFAULT 0",
                "body_fetched INTEGER DEFAULT 0", "thread_id TEXT",
                "in_reply_to TEXT", "refs TEXT"):
        _addcol(c, "messages", col)


def _m3_bodies_attachments(c):
    c.executescript("""
      CREATE TABLE IF NOT EXISTS bodies (
        message_pk INTEGER PRIMARY KEY REFERENCES messages(pk) ON DELETE CASCADE,
        raw BLOB, fetched_at TEXT);
      CREATE TABLE IF NOT EXISTS attachments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_pk INTEGER REFERENCES messages(pk) ON DELETE CASCADE,
        part_id TEXT, filename TEXT, content_type TEXT, size INTEGER,
        sha256 TEXT, is_inline INTEGER DEFAULT 0, content_id TEXT);
      CREATE INDEX IF NOT EXISTS idx_msg_acct_folder ON messages(account, folder, ts);
      CREATE INDEX IF NOT EXISTS idx_msg_thread ON messages(thread_id);
      CREATE INDEX IF NOT EXISTS idx_att_msg ON attachments(message_pk);""")


def _m4_outbox(c):
    c.executescript("""
      CREATE TABLE IF NOT EXISTS outbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
        idempotency_key TEXT NOT NULL UNIQUE, message_id TEXT NOT NULL,
        in_reply_to_pk INTEGER, envelope TEXT NOT NULL, recipients TEXT NOT NULL,
        subject TEXT, raw BLOB NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
        attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at INTEGER NOT NULL DEFAULT 0,
        delivered INTEGER NOT NULL DEFAULT 0,
        sent_appended INTEGER NOT NULL DEFAULT 0, answered_set INTEGER NOT NULL DEFAULT 0,
        last_error TEXT, created_at TEXT NOT NULL, sent_at TEXT);
      CREATE INDEX IF NOT EXISTS idx_outbox_state ON outbox(state, next_attempt_at);
      CREATE INDEX IF NOT EXISTS idx_outbox_created ON outbox(created_at);""")
    _addcol(c, "outbox", "delivered INTEGER NOT NULL DEFAULT 0")  # idempotent for an early outbox table


def _m5_thread_index(c):
    c.execute("CREATE INDEX IF NOT EXISTS idx_msg_acct_folder_thread "
              "ON messages(account, folder, thread_id)")
    import threads  # account-scoped recompute of the (currently NULL) thread_id column
    for (acct,) in c.execute("SELECT DISTINCT account FROM messages").fetchall():
        threads.recompute_threads(c, acct)


def _m6_fts(c):
    _addcol(c, "messages", "body_text TEXT")
    _addcol(c, "messages", "recipients TEXT")   # To+Cc, searchable via to:/cc:
    c.executescript(
        "CREATE VIRTUAL TABLE IF NOT EXISTS msg_fts USING fts5("
        "subject, from_name, from_addr, recipients, snippet, body_text, "
        "tokenize='unicode61 remove_diacritics 2');")
    # backfill body_text + recipients for the handful of already-fetched bodies (no IMAP)
    import mailparse
    for (pk,) in c.execute("SELECT message_pk FROM bodies").fetchall():
        raw = get_body(c, pk)
        if not raw:
            continue
        parsed = mailparse.parse(raw)
        c.execute("UPDATE messages SET body_text=?, recipients=? WHERE pk=?",
                  (_body_text_of(parsed), _recipients_of(parsed), pk))
    # (re)build the index from messages in one pass (idempotent)
    c.execute("DELETE FROM msg_fts")
    for (pk,) in c.execute("SELECT pk FROM messages").fetchall():
        fts_index(c, pk)


def _m10_recipients(c):
    """Make to:/cc: search the actual recipients instead of secretly grepping the body.
    Add the recipients column, backfill it from fetched bodies, and rebuild msg_fts WITH a
    recipients column (FTS5 has no ALTER ADD COLUMN, so drop + recreate + re-index)."""
    _addcol(c, "messages", "recipients TEXT")
    import mailparse
    for (pk,) in c.execute("SELECT message_pk FROM bodies").fetchall():
        raw = get_body(c, pk)
        if not raw:
            continue
        rcpt = _recipients_of(mailparse.parse(raw))
        if rcpt:
            c.execute("UPDATE messages SET recipients=? WHERE pk=?", (rcpt, pk))
    cols = [r[1] for r in c.execute("PRAGMA table_info(msg_fts)").fetchall()]
    if "recipients" not in cols:
        c.executescript(
            "DROP TABLE IF EXISTS msg_fts;"
            "CREATE VIRTUAL TABLE msg_fts USING fts5("
            "subject, from_name, from_addr, recipients, snippet, body_text, "
            "tokenize='unicode61 remove_diacritics 2');")
        for (pk,) in c.execute("SELECT pk FROM messages").fetchall():
            fts_index(c, pk)


def _m11_briefing_cache(c):
    """Durable cache for the AI 'Today' card, keyed on a logical (accounts|date) string
    (NOT a pk) so the daily claude call is paid once and survives restarts."""
    c.executescript(
        "CREATE TABLE IF NOT EXISTS briefing_cache (key TEXT PRIMARY KEY, fingerprint TEXT, "
        "narrative TEXT, groups_json TEXT, model TEXT, created_at TEXT);")


def _m12_mute(c):
    """Muted threads — keyed on (account, thread_id), a NATURAL key that survives the
    reconcile/reset_folder delete+reinsert of messages rows (a muted row must not be tied
    to any message pk)."""
    c.executescript(
        "CREATE TABLE IF NOT EXISTS muted (account TEXT NOT NULL, thread_id TEXT NOT NULL, "
        "muted_at TEXT, PRIMARY KEY(account, thread_id));"
        "CREATE INDEX IF NOT EXISTS idx_muted_acct ON muted(account);")


def _m13_followups(c):
    """Commitment tracking — threads awaiting a reply (sent-no-reply / received-unanswered).
    Keyed on (account, thread_id), NEVER pk (anchor_pk is advisory display-only); survives
    reconcile/reset_folder delete+reinsert like muted."""
    c.executescript(
        "CREATE TABLE IF NOT EXISTS followups (account TEXT NOT NULL, thread_id TEXT NOT NULL, "
        "kind TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', anchor_pk INTEGER, "
        "first_sent_ts INTEGER, last_msg_ts INTEGER, due_ts INTEGER, created_at TEXT, "
        "resolved_at TEXT, PRIMARY KEY(account, thread_id));"
        "CREATE INDEX IF NOT EXISTS idx_followups_state ON followups(account, state);")
    _addcol(c, "outbox", "followup_recorded INTEGER NOT NULL DEFAULT 0")


def _m14_notes(c):
    """Historical no-op: private notes shipped here and were removed 2026-07-10 (feature
    retired at the user's request; _m18 drops the table). The version slot is kept so
    migration numbering stays contiguous."""
    pass


def _m15_sync_health(c):
    """Per-account sync health: additive nullable columns on sync_state (no PK/watermark change,
    cannot regress UIDVALIDITY tracking). Idempotent via _addcol."""
    for col in ("last_ok_ts TEXT", "last_error TEXT", "last_error_ts TEXT",
                "last_error_kind TEXT", "sync_count INTEGER DEFAULT 0"):
        _addcol(c, "sync_state", col)


def _m16_read_mutations(c):
    """Offline read-side mutation queue — fresh additive table, keyed on its own id + a UNIQUE
    idempotency_key, referencing the message ONLY by message_id (never messages.pk), so
    reconcile/reset (delete+reinsert of message rows) can never corrupt it."""
    c.executescript(
        "CREATE TABLE IF NOT EXISTS read_mutations ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,"
        " idempotency_key TEXT NOT NULL UNIQUE, op TEXT NOT NULL, message_id TEXT,"
        " src_folder TEXT NOT NULL, dest TEXT, payload TEXT NOT NULL,"
        " state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,"
        " next_attempt_at INTEGER NOT NULL DEFAULT 0, applied INTEGER NOT NULL DEFAULT 0,"
        " last_error TEXT, created_at TEXT NOT NULL, done_at TEXT);"
        "CREATE INDEX IF NOT EXISTS idx_readmut_state ON read_mutations(state, next_attempt_at);")


def _m17_draft_ahead(c):
    """Durable draft-ahead cache. Keyed on (account, ref_key) where ref_key is
    't:<thread_id>' or 'm:<message_id>' — natural keys that survive reconcile/
    reset_folder (src_pk is advisory/debug only, NEVER used for lookups).
    A row with fail_ts set doubles as the failure-backoff record so an AI outage's
    backoff survives restarts (no startup regen storm)."""
    c.executescript(
        "CREATE TABLE IF NOT EXISTS draft_ahead ("
        " account        TEXT NOT NULL,"
        " ref_key        TEXT NOT NULL,"
        " fp             TEXT NOT NULL DEFAULT '',"
        " body           TEXT,"
        " tone           TEXT,"
        " model          TEXT,"
        " source         TEXT NOT NULL DEFAULT 'dwell',"
        " src_pk         INTEGER,"
        " src_message_id TEXT,"
        " created_ts     INTEGER NOT NULL DEFAULT 0,"
        " last_hit_ts    INTEGER,"
        " fail_ts        INTEGER NOT NULL DEFAULT 0,"
        " fail_count     INTEGER NOT NULL DEFAULT 0,"
        " PRIMARY KEY(account, ref_key));"
        "CREATE INDEX IF NOT EXISTS idx_draft_ahead_created ON draft_ahead(created_ts);")


def _m18_drop_notes(c):
    """Private-notes feature retired (2026-07-10, user request): drop the table _m14 created."""
    c.executescript("DROP TABLE IF EXISTS notes;")


def _m19_unsub(c):
    """Unsubscribe & sender-cleanup (2026-07-14): cache the resolved List-Unsubscribe method per
    message (lazily, at preview time) + an idempotency ledger so a repeat cleanup never re-fires an
    outbound unsubscribe. Nothing here is destructive; the block/sweep reuse rules + the undo stack."""
    _addcol(c, "messages", "list_unsub_raw TEXT")       # raw List-Unsubscribe (+ -Post) header values
    _addcol(c, "messages", "list_unsub_method TEXT")    # resolved: one-click|mailto|http-page|none
    c.executescript("""
      CREATE TABLE IF NOT EXISTS unsub_log(
        id INTEGER PRIMARY KEY,
        from_addr TEXT, method TEXT, status TEXT, http_status INTEGER,
        ts TEXT, idempotency_key TEXT UNIQUE);
      CREATE INDEX IF NOT EXISTS idx_unsub_log_addr ON unsub_log(from_addr);
    """)


def _m20_draft_resume(c):
    """Reopening a saved draft (2026-10-31). Bcc never enters a draft's bytes, so blind copies
    and the text you were editing stay on this machine, beside the draft: your own text, the
    quote or forwarded section MailMind added after it (text, and HTML, which never goes to the
    client), the Bcc chips, and the sha256 of the bytes saved, which proves a copy is MailMind's
    when the server gave no UID. Drafts saved earlier have none of it."""
    _addcol(c, "drafts", "own_text TEXT")
    _addcol(c, "drafts", "tail_text TEXT")
    _addcol(c, "drafts", "tail_html TEXT")
    _addcol(c, "drafts", "bcc TEXT")          # JSON list of chips; NULL when none
    _addcol(c, "drafts", "raw_sha TEXT")


# ---- unsubscribe / sender-cleanup helpers ----
def set_list_unsub(c, pk, raw, method):
    c.execute("UPDATE messages SET list_unsub_raw=?, list_unsub_method=? WHERE pk=?", (raw, method, pk))


def unsub_log_get(c, key):
    return c.execute("SELECT * FROM unsub_log WHERE idempotency_key=?", (key,)).fetchone()


def unsub_log_put(c, from_addr, method, status, http_status, ts, key):
    """Idempotent insert; returns the row (existing on dupe key, so a repeat never re-fires)."""
    c.execute("""INSERT OR IGNORE INTO unsub_log(from_addr,method,status,http_status,ts,idempotency_key)
                 VALUES(?,?,?,?,?,?)""", (from_addr, method, status, http_status, ts, key))
    return c.execute("SELECT * FROM unsub_log WHERE idempotency_key=?", (key,)).fetchone()


# ---- offline read-mutation queue helpers (mirror outbox_* exactly) ----
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


def read_mut_due(c, now_ts):
    return [r["id"] for r in c.execute(
        "SELECT id FROM read_mutations WHERE state='queued' AND next_attempt_at<=? ORDER BY id",
        (now_ts,))]


def read_mut_claim(c, mid):
    """Atomically flip queued->running; True only for the single winner."""
    return c.execute("UPDATE read_mutations SET state='running' WHERE id=? AND state='queued'",
                     (mid,)).rowcount == 1


def read_mut_mark_applied(c, mid):
    """The IMAP op landed — set the instant it returns, before bookkeeping, so it never re-runs."""
    c.execute("UPDATE read_mutations SET applied=1 WHERE id=?", (mid,))


def read_mut_release(c, mid, err, attempts, next_attempt_at, terminal):
    c.execute("UPDATE read_mutations SET state=?, attempts=?, next_attempt_at=?, last_error=? WHERE id=?",
              ("error" if terminal else "queued", attempts, next_attempt_at, err, mid))


def read_mut_mark_done(c, mid, done_at):
    c.execute("UPDATE read_mutations SET state='done', done_at=?, last_error=NULL WHERE id=?",
              (done_at, mid))


def read_mut_cancel_unapplied(c, mid):
    """Undo: delete a row that hasn't shipped yet (queued OR claimed-but-not-applied). The worker
    re-reads the row under the lock and skips if it's gone, so this closes the claim→cancel race."""
    return c.execute("DELETE FROM read_mutations WHERE id=? AND applied=0", (mid,)).rowcount == 1


def read_mut_set_dest(c, mid, dest):
    """Persist the destination the worker resolved online, so a later undo can find the message."""
    c.execute("UPDATE read_mutations SET dest=? WHERE id=?", (dest, mid))


def read_mut_latest_by_msgid(c, account, message_id):
    """The most recent read-mutation row for a message (any state) — undo uses it to decide
    cancel-vs-counter-move and to recover the resolved destination folder."""
    return c.execute("SELECT * FROM read_mutations WHERE account=? AND message_id=? "
                     "ORDER BY id DESC LIMIT 1", (account, message_id)).fetchone()


def read_mut_reset_stuck(c):
    """Boot recovery: running->queued. Safe because an applied=1 row skips the wire op on retry."""
    c.execute("UPDATE read_mutations SET state='queued' WHERE state='running'")


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


def _m7_snooze(c):
    _addcol(c, "messages", "snoozed_until INTEGER")
    c.execute("CREATE INDEX IF NOT EXISTS idx_msg_snooze ON messages(snoozed_until) "
              "WHERE snoozed_until IS NOT NULL")


def _m8_thread_summaries(c):
    c.executescript("""
      CREATE TABLE IF NOT EXISTS thread_summaries (
        account TEXT NOT NULL, thread_id TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'thread',
        fingerprint TEXT NOT NULL, tldr TEXT, points TEXT, action TEXT, participants TEXT,
        message_count INTEGER, model TEXT, created_at TEXT,
        PRIMARY KEY(account, thread_id, scope));""")


def _m9_drafts(c):
    c.executescript("""
      CREATE TABLE IF NOT EXISTS drafts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL, folder TEXT, uid INTEGER,
        message_id TEXT, uidvalidity INTEGER, in_reply_to_pk INTEGER, mode TEXT, subject TEXT,
        created_at TEXT, updated_at TEXT);""")
    _addcol(c, "outbox", "draft_id INTEGER")
    _addcol(c, "outbox", "draft_deleted INTEGER NOT NULL DEFAULT 0")


# (target_user_version, migration callable). Each is idempotent.
_MIGRATIONS = [
    (1, _m1_sync_state_folder),
    (2, _m2_message_flags),
    (3, _m3_bodies_attachments),
    (4, _m4_outbox),
    (5, _m5_thread_index),
    (6, _m6_fts),
    (7, _m7_snooze),
    (8, _m8_thread_summaries),
    (9, _m9_drafts),
    (10, _m10_recipients),
    (11, _m11_briefing_cache),
    (12, _m12_mute),
    (13, _m13_followups),
    (14, _m14_notes),
    (15, _m15_sync_health),
    (16, _m16_read_mutations),
    (17, _m17_draft_ahead),
    (18, _m18_drop_notes),
    (19, _m19_unsub),
    (20, _m20_draft_resume),
]

# Pre-versioning idempotent column adds (for DBs older than the current SCHEMA).
_LEGACY_COLS = [
    ("messages", "confidence TEXT"), ("messages", "reason TEXT"),
    ("messages", "verified INTEGER DEFAULT 0"), ("messages", "last_seen TEXT"),
]


def init():
    c = conn()
    c.executescript(SCHEMA)
    for table, coldef in _LEGACY_COLS:
        _addcol(c, table, coldef)
    v = c.execute("PRAGMA user_version").fetchone()[0]
    for target, fn in _MIGRATIONS:
        if v < target:
            fn(c)
            c.execute(f"PRAGMA user_version={target}")
            v = target
    c.commit()
    c.close()


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
    fts_delete(c, pk)                       # drop the FTS row first (single delete chokepoint)
    c.execute("DELETE FROM summaries WHERE message_pk=?", (pk,))
    c.execute("DELETE FROM messages WHERE pk=?", (pk,))


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


def set_uidvalidity(c, account, folder, uidvalidity):
    c.execute(
        """INSERT INTO sync_state(account,folder,uidvalidity) VALUES(?,?,?)
           ON CONFLICT(account,folder) DO UPDATE SET uidvalidity=excluded.uidvalidity""",
        (account, folder, uidvalidity),
    )


def reset_folder(c, account, folder):
    """UIDVALIDITY changed (or forced refresh): drop this folder's rows + watermark
    so it re-syncs from scratch. Summaries cascade with the messages."""
    pks = [r["pk"] for r in c.execute(
        "SELECT pk FROM messages WHERE account=? AND folder=?", (account, folder))]
    for pk in pks:
        delete_message(c, pk)
    c.execute("UPDATE sync_state SET last_uid=0 WHERE account=? AND folder=?", (account, folder))
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
    pk = cur.fetchone()[0]
    fts_index(c, pk)                        # keep the search index in sync on insert + update
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
                 envelope,recipients,subject,raw,state,next_attempt_at,draft_id,created_at)
               VALUES(:account,:idempotency_key,:message_id,:in_reply_to_pk,
                 :envelope,:recipients,:subject,:raw,'queued',:next_attempt_at,:draft_id,:created_at)
               RETURNING id""", row)
        return cur.fetchone()["id"], True
    except sqlite3.IntegrityError:   # someone inserted the same key between our SELECT and INSERT
        r = c.execute("SELECT id FROM outbox WHERE idempotency_key=?",
                      (row["idempotency_key"],)).fetchone()
        return (r["id"] if r else None), False


def outbox_mark_delivered(c, oid):
    """Record that the wire send SUCCEEDED — set the instant smtpsend.send returns,
    before any side effects, so a crash/DB-failure afterward can never re-send."""
    c.execute("UPDATE outbox SET delivered=1 WHERE id=?", (oid,))


def outbox_get(c, oid):
    return c.execute("SELECT * FROM outbox WHERE id=?", (oid,)).fetchone()


def outbox_due(c, now_ts):
    """ids of rows eligible to send now (queued + past their backoff gate)."""
    return [r["id"] for r in c.execute(
        "SELECT id FROM outbox WHERE state='queued' AND next_attempt_at<=? ORDER BY id",
        (now_ts,))]


def outbox_claim(c, oid):
    """Atomically flip queued->sending; returns True only for the single winner."""
    cur = c.execute("UPDATE outbox SET state='sending' WHERE id=? AND state='queued'", (oid,))
    return cur.rowcount == 1


def outbox_release(c, oid, err, attempts, next_attempt_at, terminal):
    """Send attempt failed: record scrubbed error + backoff, return to queue or error."""
    c.execute("""UPDATE outbox SET state=?, attempts=?, next_attempt_at=?, last_error=?
                 WHERE id=?""",
              ("error" if terminal else "queued", attempts, next_attempt_at, err, oid))


def outbox_mark_sent(c, oid, sent_at):
    c.execute("UPDATE outbox SET state='sent', sent_at=?, last_error=NULL WHERE id=?",
              (sent_at, oid))


def outbox_set(c, oid, **flags):
    cols = {k: int(v) for k, v in flags.items() if k in ("sent_appended", "answered_set", "draft_deleted", "followup_recorded")}
    if not cols:
        return
    sets = ", ".join(f"{k}=?" for k in cols)
    c.execute(f"UPDATE outbox SET {sets} WHERE id=?", [*cols.values(), oid])


def outbox_cancel(c, oid):
    """Delete a still-queued send (Undo). No-op if it's already sending/sent."""
    cur = c.execute("DELETE FROM outbox WHERE id=? AND state='queued'", (oid,))
    return cur.rowcount == 1


def outbox_requeue(c, oid):
    """Manual retry of an errored row: back to queued, clear backoff + error."""
    cur = c.execute("""UPDATE outbox SET state='queued', next_attempt_at=0, last_error=NULL
                       WHERE id=? AND state='error'""", (oid,))
    return cur.rowcount == 1


def outbox_send_now(c, oid, now_ts):
    """Bring a scheduled (queued, future next_attempt_at) send forward to now."""
    cur = c.execute("UPDATE outbox SET next_attempt_at=? WHERE id=? AND state='queued'",
                    (int(now_ts), oid))
    return cur.rowcount == 1


def outbox_list(c, limit=50):
    return c.execute(
        """SELECT id,account,subject,recipients,state,attempts,last_error,created_at,sent_at,next_attempt_at
           FROM outbox ORDER BY id DESC LIMIT ?""", (limit,)).fetchall()


# ------------------------------------------------------------------- snooze
def set_snooze(c, pk, until):
    c.execute("UPDATE messages SET snoozed_until=? WHERE pk=?", (int(until), pk))


def clear_snooze(c, pk):
    c.execute("UPDATE messages SET snoozed_until=NULL WHERE pk=?", (pk,))


def due_snoozes(c, now_ts):
    return c.execute("SELECT pk, account, folder FROM messages "
                     "WHERE snoozed_until IS NOT NULL AND snoozed_until<=?", (now_ts,)).fetchall()


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


def followup_detect_received(c, now_ts, days, max_age_days=30):
    """Inbound, seen, unanswered, important threads aging past the window with no reply from
    me, not muted/snoozed/already-tracked, within the max-age floor (avoids a first-run flood)."""
    cutoff = now_ts - days * 86400
    floor = now_ts - max_age_days * 86400
    c.execute("""INSERT OR IGNORE INTO followups(account,thread_id,kind,state,anchor_pk,last_msg_ts,due_ts,created_at)
        SELECT m.account, m.thread_id, 'received', 'due', m.pk, m.ts, ?, ?
        FROM messages m
        WHERE m.thread_id IS NOT NULL AND m.thread_id != '' AND m.seen=1 AND m.answered=0 AND m.is_junk=0
          AND m.ts <= ? AND m.ts >= ?
          AND m.pk=(SELECT pk FROM messages m2 WHERE m2.account=m.account AND m2.thread_id=m.thread_id
                    ORDER BY m2.ts DESC, m2.pk DESC LIMIT 1)
          AND m.snoozed_until IS NULL
          AND EXISTS (SELECT 1 FROM messages mi JOIN summaries s ON s.message_pk=mi.pk
                WHERE mi.account=m.account AND mi.thread_id=m.thread_id
                  AND (s.importance>=4 OR s.category='action-needed'))
          AND NOT EXISTS (SELECT 1 FROM messages mo WHERE mo.account=m.account
                AND mo.thread_id=m.thread_id AND lower(mo.from_addr)=lower(m.account))
          AND NOT EXISTS (SELECT 1 FROM muted mu WHERE mu.account=m.account AND mu.thread_id=m.thread_id)
          AND NOT EXISTS (SELECT 1 FROM followups f WHERE f.account=m.account AND f.thread_id=m.thread_id)
        """, (now_ts, datetime.now(timezone.utc).isoformat(), cutoff, floor))


def resolve_followups(c, account):
    """Auto-resolve (delete) active followups whose commitment is met: muted, or — by kind —
    an inbound reply arrived (sent) / I replied or it's marked answered (received)."""
    c.execute("""DELETE FROM followups WHERE account=? AND state IN ('pending','due') AND (
        EXISTS (SELECT 1 FROM muted mu WHERE mu.account=followups.account AND mu.thread_id=followups.thread_id)
        OR (followups.kind='sent' AND EXISTS (SELECT 1 FROM messages m WHERE m.account=followups.account
              AND m.thread_id=followups.thread_id AND m.ts > IFNULL(followups.first_sent_ts,0)
              AND lower(m.from_addr) <> lower(followups.account)))
        OR (followups.kind='received' AND EXISTS (SELECT 1 FROM messages m WHERE m.account=followups.account
              AND m.thread_id=followups.thread_id AND (m.answered=1 OR lower(m.from_addr)=lower(followups.account))))
        OR (followups.kind='replylater' AND EXISTS (SELECT 1 FROM messages m WHERE m.account=followups.account
              AND m.thread_id=followups.thread_id AND (m.answered=1 OR lower(m.from_addr)=lower(followups.account))))
    )""", (account,))


# ----- Reply Later: a user-pinned stack of threads to answer (kind='replylater') -----
def set_replylater(c, account, thread_id, anchor_pk, when):
    """Pin a thread to the Reply-Later stack. Supersedes any auto-nudge already on the thread
    (an explicit 'I'll reply' beats an aging guess), and re-activates a prior dismissal."""
    c.execute("""INSERT INTO followups(account,thread_id,kind,state,anchor_pk,last_msg_ts,created_at)
                   VALUES(?,?,'replylater','due',?,?,?)
                 ON CONFLICT(account,thread_id) DO UPDATE SET
                   kind='replylater', state='due', anchor_pk=excluded.anchor_pk,
                   last_msg_ts=excluded.last_msg_ts, created_at=excluded.created_at, resolved_at=NULL""",
              (account, thread_id, anchor_pk, anchor_pk, when))


def clear_replylater(c, account, thread_id):
    """Done / un-pin: drop the reply-later row entirely."""
    c.execute("DELETE FROM followups WHERE account=? AND thread_id=? AND kind='replylater'",
              (account, thread_id))


def list_replylater(c, account):
    """One representative (latest) message per reply-later thread, most-recently-pinned first (a stack)."""
    return c.execute(
        """SELECT m.*, f.created_at AS f_created,
                  (SELECT COUNT(*) FROM messages mm WHERE mm.account=m.account AND mm.thread_id=m.thread_id) AS thread_count
           FROM followups f JOIN messages m ON m.account=f.account AND m.thread_id=f.thread_id
           WHERE f.account=? AND f.kind='replylater'
             AND m.pk=(SELECT pk FROM messages m2 WHERE m2.account=m.account AND m2.thread_id=m.thread_id
                       ORDER BY m2.ts DESC, m2.pk DESC LIMIT 1)
           ORDER BY f.created_at DESC""", (account,)).fetchall()


def replylater_count(c, account):
    # match list_replylater's messages JOIN, so the badge can't exceed the list (a reply-later row
    # whose thread has no surviving message rows after reconcile is not shown, so don't count it)
    return c.execute("SELECT COUNT(*) FROM followups f WHERE f.account=? AND f.kind='replylater' "
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
    """Does the server flag this message \\Draft (as last synced)? A folder whose name merely
    contains "draft" can hold received mail, which must never be removed as a replaced draft."""
    r = c.execute("SELECT draft FROM messages WHERE account=? AND folder=? AND uid=?", (account, folder, uid)).fetchone()
    return bool(r and r["draft"])


def draft_repoint(c, did, folder, uid, uidvalidity, forget=False):
    """Track the copy that was opened rather than the one last saved: another app re-saved the
    draft under the same Message-ID, so the next save or send must replace this one. forget: the
    copy isn't byte-for-byte what MailMind saved, so the recorded text and Bcc no longer describe
    it. The recorded quote stays; it is only ever trusted where the text still ends with it."""
    c.execute("UPDATE drafts SET folder=?, uid=?, uidvalidity=? WHERE id=?", (folder, uid, uidvalidity, did))
    if forget:
        c.execute("UPDATE drafts SET own_text=NULL, bcc=NULL, raw_sha=NULL WHERE id=?", (did,))


def draft_delete(c, did):
    c.execute("DELETE FROM drafts WHERE id=?", (did,))


def outbox_reset_stuck(c):
    """On boot, any row left 'sending' (crash mid-send) returns to queued. Safe
    against double-send because send_one() skips the wire send when delivered=1 and
    only finishes the bookkeeping/side-effects; a delivered=0 row truly never sent."""
    c.execute("UPDATE outbox SET state='queued' WHERE state='sending'")


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
           WHERE m.is_junk = 0 AND m.snoozed_until IS NULL AND s.importance >= ?
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
