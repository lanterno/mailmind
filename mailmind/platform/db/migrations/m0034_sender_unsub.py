"""Migration 34 (2026-11-05): how to unsubscribe from a sender outlives the sender's mail.

A cleanup sweeps the sender's mail away and then tries the unsubscribe. One that failed could only be
tried again by reading the headers off the rows the sweep had just removed, and so found no way to.
`sender_unsub` keeps what the newest message said, as `mailparse.list_unsub` wrote it, and which accounts
the sender wrote to.
"""


def migrate(c):
    c.execute("""CREATE TABLE IF NOT EXISTS sender_unsub(
                   from_addr TEXT PRIMARY KEY,
                   raw TEXT NOT NULL,
                   accounts TEXT NOT NULL,
                   updated_at TEXT NOT NULL)""")
