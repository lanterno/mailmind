"""Migration 44 (2026-11-07): a send names its original by the key that outlasts a row.

A send finds the message it replies to by `orig_account` and `orig_message_id`. One with no Message-ID to go by had only
`outbox.in_reply_to_pk`, and a renumber can give a pk to another message. Now `original_key` names it, as
`messages.message_key` does, which a message with no Message-ID gets from its envelope. That was the last read of the pk.

A send still to go is read here, while its pk is all it has: a reply to a message with no Message-ID, if the pk still names
such a message in its account, and one queued before migration 27, which names nothing to check it against and goes by its
pk as it always has. A send that has gone owes nothing, and its pk may be another message's by now, so it stays as it is.
"""
from mailmind.platform.db.migrations.m0001_m0020 import _addcol


def migrate(c):
    _addcol(c, "outbox", "original_key INTEGER")
    c.execute("""UPDATE outbox SET original_key=(SELECT m.message_key FROM messages m
                                                 WHERE m.pk=outbox.in_reply_to_pk AND m.account=outbox.orig_account
                                                   AND (m.message_id IS NULL OR m.message_id=''))
                 WHERE state<>'sent' AND original_key IS NULL AND orig_account IS NOT NULL
                   AND (orig_message_id IS NULL OR orig_message_id='') AND in_reply_to_pk IS NOT NULL""")
    c.execute("""UPDATE outbox SET orig_account=m.account, orig_message_id=m.message_id, orig_thread_id=m.thread_id,
                                   original_key=m.message_key
                 FROM messages m
                 WHERE m.pk=outbox.in_reply_to_pk AND outbox.state<>'sent' AND outbox.orig_account IS NULL""")
