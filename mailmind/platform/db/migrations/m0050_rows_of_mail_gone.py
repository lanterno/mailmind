"""Migration 50 (2026-11-12): what deleting a message would have taken with it, gone.

A row that names a message by `REFERENCES messages(pk) ON DELETE CASCADE` goes when the message does, but only on a connection
that enforces foreign keys, and an older MailMind didn't always open one that did. So a database can still hold rows of mail
deleted long ago, a stored body among them: a person's mail kept after they let it go. This deletes each such row, in every
table that names a message that way, as the delete would have. Rows that name a message still there are untouched.
"""


def _cascading_from_messages(c):
    """(table, column) for each column that names a message and goes with it."""
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for table in tables:
        for _id, _seq, parent, column, to, _on_update, on_delete, _match in c.execute(f'PRAGMA foreign_key_list("{table}")'):
            if parent == "messages" and to == "pk" and on_delete == "CASCADE":
                yield table, column


def migrate(c):
    for table, column in list(_cascading_from_messages(c)):
        c.execute(f'DELETE FROM "{table}" WHERE "{column}" IS NOT NULL AND "{column}" NOT IN (SELECT pk FROM messages)')
