"""Migration 30 (2026-11-05): a message has one identity, however many copies of it there are.

A row is one copy in one folder, and what hangs on its pk goes with it. Archiving deleted the row and its
AI assessment, so an Undo, or the next sync of the archive, paid to assess the same mail again.
`message_identity` mints a key once per account and Message-ID, `messages.message_key` names it on each
copy, and `assessments` holds what the AI said under that key. `gone_at` says since when no copy is left:
the key waits a while for one to come back, then goes, and its assessment with it.
"""
from hashlib import sha256

from mailmind.platform.db.migrations.m0001_m0020 import _addcol


def _identity(message_id, from_addr, date_iso, subject):      # frozen from mailmind.mailbox.domain.identity
    written = "".join((message_id or "").split())
    if written:
        return written
    return "~" + sha256("|".join((from_addr or "", date_iso or "", subject or "")).encode()).hexdigest()


def migrate(c):
    c.execute("""CREATE TABLE IF NOT EXISTS message_identity(
                   key INTEGER PRIMARY KEY AUTOINCREMENT,
                   account TEXT NOT NULL,
                   identity TEXT NOT NULL,
                   gone_at INTEGER,
                   UNIQUE(account, identity))""")
    c.execute("""CREATE TABLE IF NOT EXISTS assessments(
                   message_key INTEGER PRIMARY KEY REFERENCES message_identity(key) ON DELETE CASCADE,
                   summary TEXT, importance INTEGER, category TEXT, action TEXT, model TEXT, created_at TEXT)""")
    _addcol(c, "messages", "message_key INTEGER")
    c.execute("CREATE INDEX IF NOT EXISTS idx_messages_key ON messages(message_key)")
    rows = c.execute("SELECT pk, account, message_id, from_addr, date_iso, subject FROM messages "
                     "WHERE message_key IS NULL").fetchall()
    for pk, account, message_id, from_addr, date_iso, subject in rows:
        identity = _identity(message_id, from_addr, date_iso, subject)
        c.execute("INSERT OR IGNORE INTO message_identity(account, identity) VALUES(?,?)", (account, identity))
        c.execute("UPDATE messages SET message_key=(SELECT key FROM message_identity WHERE account=? AND identity=?) "
                  "WHERE pk=?", (account, identity, pk))
    c.execute("""INSERT OR IGNORE INTO assessments(message_key, summary, importance, category, action, model, created_at)
                 SELECT m.message_key, s.summary, s.importance, s.category, s.action, s.model, s.created_at
                 FROM summaries s JOIN messages m ON m.pk=s.message_pk ORDER BY s.created_at DESC""")
