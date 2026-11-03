"""Migrations 1 to 20, frozen: the base schema, the pre-versioning columns and the migrations as
store.py held them when the package took them over (2026-11-03), with copies of the code they
call, so a change to the live code can never change what an old migration does to a database.

Never edit this file. A schema change is a new migration, after these.
"""
import re as _re
import sqlite3

from mailmind.platform.db.migrations import _frozen_mailparse, _frozen_threads

# The schema a database starts from; the migrations bring it forward from there.
BASE_SCHEMA = """
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


# Pre-versioning idempotent column adds (for DBs older than the current SCHEMA).
LEGACY_COLS = [
    ("messages", "confidence TEXT"), ("messages", "reason TEXT"),
    ("messages", "verified INTEGER DEFAULT 0"), ("messages", "last_seen TEXT"),
]


# -- what the migrations call, as it was ---------------------------------------

def _has_col(c, table, col):
    return col in [r[1] for r in c.execute(f"PRAGMA table_info({table})")]


def _addcol(c, table, coldef):
    try:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {coldef}")
    except sqlite3.OperationalError:
        pass  # already there


def get_body(c, pk):
    r = c.execute("SELECT raw FROM bodies WHERE message_pk=?", (pk,)).fetchone()
    return r["raw"] if r else None


_TAG = _re.compile(r"<[^>]+>")

_NOTEXT = _re.compile(r"(?is)<(script|style|head|title)\b[^>]*>.*?</\1>")

_COMMENT = _re.compile(r"(?s)<!--.*?-->")

_FTS_CAP = 100_000  # bound a single body's indexed text


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



# -- the migrations ------------------------------------------------------------

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
    threads = _frozen_threads  # account-scoped recompute of the (currently NULL) thread_id column
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
    mailparse = _frozen_mailparse
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


def _m10_recipients(c):
    """Make to:/cc: search the actual recipients instead of secretly grepping the body.
    Add the recipients column, backfill it from fetched bodies, and rebuild msg_fts WITH a
    recipients column (FTS5 has no ALTER ADD COLUMN, so drop + recreate + re-index)."""
    _addcol(c, "messages", "recipients TEXT")
    mailparse = _frozen_mailparse
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


# (target_user_version, migration callable). Each is idempotent.
MIGRATIONS = [
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
