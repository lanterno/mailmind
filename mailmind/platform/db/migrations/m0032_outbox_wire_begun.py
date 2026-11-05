"""Migration 32 (2026-11-05): whether a send's last bytes may have left.

A connection lost after the whole message went out, or a crash in that moment, left the outbox unable
to tell whether the server took it, and it sent again, so recipients could get it twice.
`outbox.wire_begun` is set just before those bytes go. A row a crash left with it set goes to a person
instead of back to the queue.
"""
from mailmind.platform.db.migrations.m0001_m0020 import _addcol


def migrate(c):
    _addcol(c, "outbox", "wire_begun INTEGER NOT NULL DEFAULT 0")
