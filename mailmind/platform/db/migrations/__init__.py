"""The schema's history. A database records the last migration it has run in `PRAGMA
user_version`; `MIGRATIONS` lists (version, migration) in order, and only ever grows at the end:
the version number is the on-disk contract, so a migration is never renumbered or removed.

Migrations 1 to 20 are frozen in `m0001_m0020`. Each later one has a module of its own,
`m<NNNN>_<what>.py`, with a `migrate(c)` that is idempotent and never commits.
"""
from mailmind.platform.db.migrations import (m0021_ai_usage, m0022_thread_fingerprints, m0023_pending_removal,
                                             m0024_scan_state, m0025_undo_entries, m0026_draft_key,
                                             m0027_reply_original, m0028_attached_email_parts, m0029_unsub_accounts,
                                             m0030_message_identity, m0031_highest_modseq,
                                             m0032_outbox_wire_begun, m0033_snoozes, m0034_sender_unsub,
                                             m0035_message_recipients, m0036_ai_batches, m0037_ai_answers,
                                             m0038_contentless_search, m0039_mail_commands, m0040_draft_journal, m0041_outbox_wait_kind,
                                             m0042_thread_links, m0043_assessment_record, m0044_outbox_original_key,
                                             m0045_rollback_columns, m0046_tasks, m0047_message_labels,
                                             m0048_documents_and_audit)
from mailmind.platform.db import driver
from mailmind.platform.db.migrations import m0001_m0020
from mailmind.platform.db.migrations.m0001_m0020 import BASE_SCHEMA, LEGACY_COLS, _addcol
from mailmind.platform.db.migrations.m0001_m0020 import MIGRATIONS as _FROZEN

# The frozen migrations name the standard library's sqlite3 in their `except` clauses, written before the database was
# encrypted. The driver raises its own classes, which those would never catch, and the file is never edited: so its
# name `sqlite3` is the driver, and each clause catches what it always meant to.
m0001_m0020.sqlite3 = driver  # type: ignore[attr-defined]

__all__ = ["BASE_SCHEMA", "BASE_UNTIL", "FILLED", "LEGACY_COLS", "MIGRATIONS", "REBUILT", "RETIRED", "TRIMMED", "add_column"]

add_column = _addcol

MIGRATIONS = [
    *_FROZEN,
    (21, m0021_ai_usage.migrate),
    (22, m0022_thread_fingerprints.migrate),
    (23, m0023_pending_removal.migrate),
    (24, m0024_scan_state.migrate),
    (25, m0025_undo_entries.migrate),
    (26, m0026_draft_key.migrate),
    (27, m0027_reply_original.migrate),
    (28, m0028_attached_email_parts.migrate),
    (29, m0029_unsub_accounts.migrate),
    (30, m0030_message_identity.migrate),
    (31, m0031_highest_modseq.migrate),
    (32, m0032_outbox_wire_begun.migrate),
    (33, m0033_snoozes.migrate),
    (34, m0034_sender_unsub.migrate),
    (35, m0035_message_recipients.migrate),
    (36, m0036_ai_batches.migrate),
    (37, m0037_ai_answers.migrate),
    (38, m0038_contentless_search.migrate),
    (39, m0039_mail_commands.migrate),
    (40, m0040_draft_journal.migrate),
    (41, m0041_outbox_wait_kind.migrate),
    (42, m0042_thread_links.migrate),
    (43, m0043_assessment_record.migrate),
    (44, m0044_outbox_original_key.migrate),
    (45, m0045_rollback_columns.migrate),
    (46, m0046_tasks.migrate),
    (47, m0047_message_labels.migrate),
    (48, m0048_documents_and_audit.migrate),
]

BASE_UNTIL = _FROZEN[-1][0]     # a database at this version or later has all the base schema gave it
RETIRED = {"notes": 18, "briefing_cache": 37, "read_mutations": 39, "summaries": 45}     # a table a migration ended, and that migration's version
REBUILT = {"msg_fts": 38}                         # one a migration built afresh, so its count may change
FILLED = {"assessments": 45, "snoozes": 45, "message_identity": 45}     # one a migration may add rows to, carrying over what it ends
# One a migration takes rows from on purpose: that migration's version, and how to count what it will take, asked beforehand.
TRIMMED = {"attachments": (28, "SELECT count(*) FROM attachments WHERE message_pk IN "
                               "(SELECT message_pk FROM attachments WHERE lower(content_type) LIKE 'message/%')")}
