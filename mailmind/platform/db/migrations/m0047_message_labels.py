"""Migration 47 (2026-11-08): the labels on each copy of a Gmail message.

A Gmail message can be under many labels, and its labels were read only to write them (archive drops \\Inbox). Now a sync's header
fetch reads X-GM-LABELS for Gmail accounts, and this holds one row for each label on each copy, going with the copy. It is no part
of a message's flags: reading flags again, online or off, touches nothing here.

Mail stored before this has none until a scan fetches it again, as what a sync fetches is all it reads: a separate walk of a big
All Mail to read labels would cost more than it brings.
"""


def migrate(c):
    c.execute("""CREATE TABLE IF NOT EXISTS message_labels(
                   message_pk INTEGER NOT NULL REFERENCES messages(pk) ON DELETE CASCADE,
                   label TEXT NOT NULL,
                   PRIMARY KEY(message_pk, label))""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_message_labels_label ON message_labels(label)")
