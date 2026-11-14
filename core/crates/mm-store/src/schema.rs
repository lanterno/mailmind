//! The tables a message list reads, exactly as a version-51 database made by the Python app holds them.
//!
//! The core does not migrate yet: these are for fixtures and tests. When the core takes over the database, its
//! migrations continue from 52 and this file goes.

/// The version of the Python app's database this core is written for: `PRAGMA user_version` once its migrations have
/// run (`tests/golden/schema_v51.sql`). The core doesn't migrate yet, so the Rust server opens no other.
pub const VERSION: i64 = 51;

pub const LIST_TABLES: &str = "
CREATE TABLE IF NOT EXISTS messages (
  pk           INTEGER PRIMARY KEY AUTOINCREMENT,
  account      TEXT NOT NULL,
  kind         TEXT NOT NULL,
  folder       TEXT NOT NULL,
  uid          INTEGER NOT NULL,
  message_id   TEXT,
  gmail_thread TEXT,
  thread_id    TEXT,
  in_reply_to  TEXT, refs TEXT,
  from_addr    TEXT, from_name TEXT,
  subject      TEXT,
  date_iso     TEXT, ts INTEGER,
  snippet      TEXT,
  bucket       TEXT,
  confidence   TEXT,
  reason       TEXT,
  is_junk      INTEGER DEFAULT 0,
  verified     INTEGER DEFAULT 0,
  last_seen    TEXT,
  seen         INTEGER DEFAULT 0,
  flagged      INTEGER DEFAULT 0,
  answered     INTEGER DEFAULT 0,
  draft        INTEGER DEFAULT 0,
  deleted      INTEGER DEFAULT 0,
  has_attach   INTEGER DEFAULT 0,
  body_fetched INTEGER DEFAULT 0,
  body_text    TEXT,
  recipients TEXT, list_unsub_raw TEXT, list_unsub_method TEXT, pending_removal INTEGER, message_key INTEGER,
  UNIQUE(account, folder, uid)
);
CREATE TABLE IF NOT EXISTS message_identity(
  key INTEGER PRIMARY KEY AUTOINCREMENT,
  account TEXT NOT NULL,
  identity TEXT NOT NULL,
  gone_at INTEGER,
  UNIQUE(account, identity));
CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(ts);
CREATE INDEX IF NOT EXISTS idx_msg_junk ON messages(is_junk);
CREATE INDEX IF NOT EXISTS idx_msg_acct_folder ON messages(account, folder, ts);
CREATE INDEX IF NOT EXISTS idx_msg_thread ON messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_msg_acct_folder_thread ON messages(account, folder, thread_id);
CREATE INDEX IF NOT EXISTS idx_messages_key ON messages(message_key);
CREATE INDEX IF NOT EXISTS idx_msg_listed ON messages(folder, ts DESC, pk DESC, account) WHERE pending_removal IS NULL;
";
