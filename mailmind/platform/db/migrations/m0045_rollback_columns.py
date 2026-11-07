"""Migration 45 (2026-11-07): what was written only for a rollback to an older commit goes.

`summaries` gave way to `assessments` (m30), `messages.snoozed_until` to `snoozes` (m33), and nothing has read
`outbox.in_reply_to_pk`, `drafts.in_reply_to_pk`, `draft_ahead.src_pk` or `followups.anchor_pk` since m44: each names a
message by a pk, which a renumber can give to another. The owner agreed to the drop on 2026-11-07. Going back to an older
commit now takes the backup the upgrade made.

A run of an older commit wrote to `summaries` and `messages.snoozed_until` alone, of a row with or without a key, and until
now each start brought that across (`store.repair_keys`, which runs after the migrations). So this does it first, whatever
has no key getting one, and a key it comes back to is no longer gone. What is already in `assessments` or `snoozes` stands.
A summary whose message has gone has no key to go under, and nothing could read it. The four pk columns hold nothing else.
"""
from mailmind.platform.db.migrations.m0001_m0020 import _has_col
from mailmind.platform.db.migrations.m0030_message_identity import _identity

_PK_COLUMNS = (("outbox", "in_reply_to_pk"), ("drafts", "in_reply_to_pk"), ("draft_ahead", "src_pk"), ("followups", "anchor_pk"))


def _give_every_row_a_key(c):
    rows = c.execute("SELECT pk, account, message_id, from_addr, date_iso, subject FROM messages "
                     "WHERE message_key IS NULL").fetchall()
    for pk, account, message_id, from_addr, date_iso, subject in rows:
        who = (account, _identity(message_id, from_addr, date_iso, subject))
        c.execute("INSERT OR IGNORE INTO message_identity(account, identity) VALUES(?,?)", who)
        key = c.execute("SELECT key FROM message_identity WHERE account=? AND identity=?", who).fetchone()[0]
        c.execute("UPDATE message_identity SET gone_at=NULL WHERE key=? AND gone_at IS NOT NULL", (key,))
        c.execute("UPDATE messages SET message_key=? WHERE pk=?", (key, pk))


def migrate(c):
    _give_every_row_a_key(c)
    if _has_col(c, "summaries", "message_pk"):
        c.execute("""INSERT OR IGNORE INTO assessments(message_key, summary, importance, category, action, model, created_at)
                     SELECT m.message_key, s.summary, s.importance, s.category, s.action, s.model, s.created_at
                     FROM summaries s JOIN messages m ON m.pk=s.message_pk
                     ORDER BY s.created_at DESC""")
        c.execute("DROP TABLE summaries")
    c.execute("DROP INDEX IF EXISTS idx_msg_snooze")          # DROP COLUMN refuses a column an index names
    if _has_col(c, "messages", "snoozed_until"):
        c.execute("""INSERT OR IGNORE INTO snoozes(message_key, folder, until)
                     SELECT message_key, folder, snoozed_until FROM messages WHERE snoozed_until IS NOT NULL""")
        c.execute("ALTER TABLE messages DROP COLUMN snoozed_until")
    for table, column in _PK_COLUMNS:
        if _has_col(c, table, column):
            c.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
