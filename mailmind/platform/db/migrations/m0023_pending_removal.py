"""Migration 23 (2026-11-05): a message a queued move will take away keeps its row until the
move settles.

An archive, trash, junk or move asked for while the server is away is queued (`read_mutations`),
and its message used to leave the list by losing its row at once. When the server then refused
the move, the queue gave up on it, or Undo cancelled it while the server was still away, there was
no row to bring back: the message stayed in its folder on the server but out of MailMind's list,
and an incremental sync, which looks only above the watermark, never fetched it again.

`messages.pending_removal` names the queued move instead (its `read_mutations.id`). The lists
leave such a row out; the queue clears the mark when the move doesn't happen, and deletes the row
once it has. NULL, as every row starts, is a message that stays.
"""
from mailmind.platform.db.migrations.m0001_m0020 import _addcol


def migrate(c):
    _addcol(c, "messages", "pending_removal INTEGER")
