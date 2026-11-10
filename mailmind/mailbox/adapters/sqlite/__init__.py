"""The mail MailMind has stored: messages, their bodies, per-folder sync state, and the command log's rows. Every function but
conn and init takes its caller's connection and leaves the commit to it. A module for each concern, and this names what
the package offers, so the app says `store.x` whichever module holds it."""
from mailmind.mailbox.adapters.sqlite.assistant_reads import (any_copy, brief_rows, conversation_rows, delta_rows,
    draft_candidates, handled_since, message_stamp, message_to_reply_to, needs_you_rows, received_by_bucket, received_since,
    sender_recent, sender_stats, sent_since, thread_anchor_pk, thread_stamps)
from mailmind.mailbox.adapters.sqlite.bound import bind_index, bind_merges
from mailmind.mailbox.adapters.sqlite.command_log import (archives_undone, command_forget_finished, command_get,
    command_insert, command_latest_move, command_mark_begun, command_mark_reversed, command_never_begun, command_open,
    command_open_counts, command_ran_under, command_record, command_set_params, flag_changes_waiting, flag_command,
    queue_flags)
from mailmind.mailbox.adapters.sqlite.connection import conn, init
from mailmind.mailbox.adapters.sqlite.contents import (attachment_of, bodies_to_fetch, delete_attachments,
    fts_set_body, get_body, has_attachments, insert_attachments, labels_of_account, mark_has_attachments, recipients_of,
    set_ingested_headers, set_labels, set_recipients, set_recipients_text, still_without_body, store_body)
from mailmind.mailbox.adapters.sqlite.flags import (classified_from_address, classified_from_domain, flag_rows_newest,
    flag_rows_of, set_classification, set_flags_if_still, set_local_flags)
from mailmind.mailbox.adapters.sqlite.lists import (digest, folder_conversation_count, folder_conversation_rows, folder_count,
    folder_rows, inboxes_conversation_count, inboxes_conversation_rows, inboxes_count, inboxes_rows, snoozed_count,
    snoozed_rows, stats, stored_accounts, stored_folder_counts, thread_rows)
from mailmind.mailbox.adapters.sqlite.messages import (copy_in_folder, copy_now, copy_pk, delete_by_uids,
    delete_message, forget_gone_messages, get_message, get_message_by_key, get_message_by_msgid, has_copy_in, is_known,
    is_listed_in, mark_pending_removal, mark_verified, message_pk, mint_key, names_of, newest_copy_in, pending_removal_pks,
    pk_in, reconcile, repair_keys, restore_pending, set_list_unsub, stored_uids, thread_pks_in, upsert_message)
from mailmind.mailbox.adapters.sqlite.sync_state import (account_health, clear_sync_error, get_modseq, get_scan_state,
    get_uidvalidity, get_watermark, record_sync_result, reset_folder, set_modseq, set_scan_state, set_uidvalidity,
    set_watermark, synced_folders)
from mailmind.mailbox.adapters.sqlite.threads import needs_threading, recompute_threads, thread_stored

__all__ = [
    "account_health", "any_copy", "archives_undone", "attachment_of", "bind_index",
    "bind_merges", "bodies_to_fetch", "brief_rows", "classified_from_address", "classified_from_domain", "clear_sync_error",
    "command_forget_finished", "command_get", "command_insert", "command_latest_move", "command_mark_begun",
    "command_mark_reversed", "command_never_begun", "command_open", "command_open_counts", "command_ran_under",
    "command_record", "command_set_params", "conn", "conversation_rows", "copy_in_folder", "copy_now", "copy_pk",
    "delete_attachments", "delete_by_uids", "delete_message", "delta_rows", "digest", "draft_candidates",
    "flag_changes_waiting", "flag_command", "flag_rows_newest", "flag_rows_of", "folder_conversation_count",
    "folder_conversation_rows", "folder_count", "folder_rows", "forget_gone_messages", "fts_set_body", "get_body",
    "get_message", "get_message_by_key", "get_message_by_msgid", "get_modseq", "get_scan_state", "get_uidvalidity",
    "get_watermark", "handled_since", "has_attachments", "has_copy_in", "inboxes_conversation_count",
    "inboxes_conversation_rows", "inboxes_count", "inboxes_rows", "init", "insert_attachments", "is_known", "is_listed_in",
    "labels_of_account", "mark_has_attachments", "mark_pending_removal", "mark_verified", "message_pk", "message_stamp",
    "message_to_reply_to", "mint_key", "names_of", "needs_threading", "needs_you_rows", "newest_copy_in", "pending_removal_pks",
    "pk_in", "queue_flags", "received_by_bucket", "received_since", "recipients_of", "recompute_threads", "reconcile",
    "record_sync_result", "repair_keys", "reset_folder", "restore_pending", "sender_recent", "sender_stats", "sent_since",
    "set_classification", "set_flags_if_still", "set_ingested_headers", "set_labels", "set_list_unsub", "set_local_flags",
    "set_modseq", "set_recipients", "set_recipients_text", "set_scan_state", "set_uidvalidity", "set_watermark",
    "snoozed_count", "snoozed_rows", "stats", "still_without_body", "store_body", "stored_accounts", "stored_folder_counts",
    "stored_uids", "synced_folders", "thread_anchor_pk", "thread_pks_in", "thread_rows", "thread_stamps", "thread_stored",
    "upsert_message",
]
