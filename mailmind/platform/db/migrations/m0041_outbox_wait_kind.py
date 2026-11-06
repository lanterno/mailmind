"""Migration 41 (2026-11-06): the outbox says what a queued send is waiting for.

Whether a send was scheduled was worked out from how far off it was due, so one backing off for a few minutes after a
failure read as scheduled too. `wait_kind` says it: `grace` (Undo Send's window), `scheduled` (a time someone picked) or
`backoff` (a try failed). A row already queued is read the old way once, when it has never failed a try.
"""
from mailmind.platform.db.migrations.m0001_m0020 import _addcol


def migrate(c):
    _addcol(c, "outbox", "wait_kind TEXT NOT NULL DEFAULT 'grace'")
    c.execute("""UPDATE outbox SET wait_kind='scheduled' WHERE state='queued' AND attempts=0
                 AND next_attempt_at > CAST(strftime('%s','now') AS INTEGER) + 15""")
    c.execute("UPDATE outbox SET wait_kind='backoff' WHERE state='queued' AND attempts>0")
