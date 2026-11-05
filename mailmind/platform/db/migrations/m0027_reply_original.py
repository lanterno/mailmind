"""Migration 27 (2026-11-05): a queued reply names its original by Message-ID too.

A reply went into the outbox naming its original by pk alone, and the row behind that pk may be gone
by the time the send goes out: archiving right after replying deletes it, and reconcile prunes or
renumbers it. Then the original got no \\Answered and the conversation no follow-up. `orig_account`,
`orig_message_id` and `orig_thread_id` hold what is durable about it, copied as the send is queued.
A row queued before this has none and goes on by pk.
"""
from mailmind.platform.db.migrations.m0001_m0020 import _addcol


def migrate(c):
    _addcol(c, "outbox", "orig_account TEXT")
    _addcol(c, "outbox", "orig_message_id TEXT")
    _addcol(c, "outbox", "orig_thread_id TEXT")
