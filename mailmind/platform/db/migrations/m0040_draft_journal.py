"""Migration 40 (2026-11-06): drafts are local first (decision D5).

A save went straight to the server, so it failed with the server and each one cost an APPEND. `draft_journal` holds what
a draft still asks of its server, one row a draft, so that the newest save replaces the one before it: the bytes of a
save not yet pushed, with the record `drafts` keeps of a copy, or `discard`. `drafts.version` counts the saves, and a save
naming another raises a conflict rather than overwriting one it never saw, unless the composer in `saved_by` made it.
`pushed_at` spaces the pushes while someone types.
"""
from mailmind.platform.db.migrations.m0001_m0020 import _addcol


def migrate(c):
    _addcol(c, "drafts", "version INTEGER NOT NULL DEFAULT 0")
    _addcol(c, "drafts", "saved_by TEXT")
    _addcol(c, "drafts", "pushed_at INTEGER")
    c.execute("""CREATE TABLE IF NOT EXISTS draft_journal(
                   draft_id   INTEGER PRIMARY KEY REFERENCES drafts(id) ON DELETE CASCADE,
                   version    INTEGER NOT NULL,
                   account    TEXT NOT NULL,              -- where the copy goes: From may have changed since the last
                   discard    INTEGER NOT NULL DEFAULT 0, -- 1: all that's left is to remove the copy
                   message_id TEXT,
                   raw        BLOB,
                   raw_sha    TEXT,
                   own_text   TEXT,
                   tail_text  TEXT,
                   tail_html  TEXT,
                   bcc        TEXT,
                   tried      TEXT,                       -- the Message-ID of a copy a try may have left
                   saved_at   INTEGER NOT NULL,
                   due_at     INTEGER NOT NULL,
                   attempts   INTEGER NOT NULL DEFAULT 0
                 )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_draft_journal_due ON draft_journal(due_at)")
