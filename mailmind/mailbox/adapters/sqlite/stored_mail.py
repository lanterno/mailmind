"""What the Mailbox offers the other contexts of the mail it has stored: every function their wiring may call, and no others.

The Assistant reads a brief's, a delta's and a draft's rows, Attention marks a message and Compose and Curation take a copy's
row with the copy. A new crossing is an edit here and to the list tests/unit/test_architecture.py pins, so it is decided
and doesn't just happen."""
from mailmind.mailbox.adapters.sqlite.assistant_reads import (any_copy, brief_rows, conversation_rows, delta_rows,
    draft_candidates, handled_since, message_stamp, message_to_reply_to, needs_you_rows, received_by_bucket, received_since,
    sender_recent, sender_stats, sent_since, thread_anchor_pk, thread_stamps)
from mailmind.mailbox.adapters.sqlite.command_log import queue_flags
from mailmind.mailbox.adapters.sqlite.contents import get_body
from mailmind.mailbox.adapters.sqlite.flags import set_local_flags
from mailmind.mailbox.adapters.sqlite.messages import (copy_in_folder, copy_now, delete_by_uids, delete_message, get_message,
    get_message_by_key, get_message_by_msgid, set_list_unsub)
from mailmind.mailbox.adapters.sqlite.sync_state import get_uidvalidity

__all__ = [
    "any_copy", "brief_rows", "conversation_rows", "copy_in_folder", "copy_now", "delete_by_uids", "delete_message", "delta_rows",
    "draft_candidates", "get_body", "get_message", "get_message_by_key", "get_message_by_msgid", "get_uidvalidity",
    "handled_since", "message_stamp", "message_to_reply_to", "needs_you_rows", "queue_flags", "received_by_bucket",
    "received_since", "sender_recent", "sender_stats", "sent_since", "set_list_unsub", "set_local_flags", "thread_anchor_pk",
    "thread_stamps",
]
