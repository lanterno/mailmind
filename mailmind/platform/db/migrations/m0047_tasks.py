"""Migration 47 (2026-11-08): the tasks a person confirmed from their mail.

A task is theirs, and outlives the message: a filing, a renumber or the message leaving the server only changes what a task
links to. It keys on the message's key (a pk isn't stable, and a key is never used again) and keeps the folder the message was
read in, to say which copy of several to link back to. No foreign key to `message_identity`: that row goes a month after the
last copy does, and the task stays.

One message and one title are one task, so confirming the same suggestion again finds its row.
"""


def migrate(c):
    c.execute("""CREATE TABLE IF NOT EXISTS tasks(
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   account TEXT NOT NULL,
                   message_key INTEGER NOT NULL,
                   folder TEXT NOT NULL,
                   title TEXT NOT NULL,
                   due TEXT,
                   done_at TEXT,
                   created_at TEXT NOT NULL,
                   UNIQUE(message_key, title))""")
