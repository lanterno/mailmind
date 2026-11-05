"""Migration 33 (2026-11-05): a snooze outlives the row it was set on.

`messages.snoozed_until` went when the row did, so a resync of the folder, or a renumber, brought a
snoozed message back into the inbox early. `snoozes` holds it by message key and folder: a copy that
arrives in that folder takes it up again, and a copy elsewhere (the message archived) doesn't. The column
stays, as what the lists filter on.
"""


def migrate(c):
    c.execute("""CREATE TABLE IF NOT EXISTS snoozes(
                   message_key INTEGER NOT NULL REFERENCES message_identity(key) ON DELETE CASCADE,
                   folder TEXT NOT NULL,
                   until INTEGER NOT NULL,
                   PRIMARY KEY(message_key, folder))""")
    c.execute("""INSERT OR IGNORE INTO snoozes(message_key, folder, until)
                 SELECT message_key, folder, snoozed_until FROM messages
                 WHERE snoozed_until IS NOT NULL AND message_key IS NOT NULL""")
