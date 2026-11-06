"""The one fingerprint every stored AI answer is checked against, and migration 22, which moved thread
summaries onto it. Fictional Message-IDs only."""
import sqlite3
import zlib

from mailmind.assistant.domain import fingerprint
from mailmind.platform.db import migrate, migrations
from mailmind.platform.db.migrations import m0022_thread_fingerprints as m22

A, B, C = "<a@example.test>", "<b@example.test>", "<c@example.test>"
ADA = "ada@example.test"


def test_the_same_messages_in_another_order_have_the_same_fingerprint():
    assert fingerprint.of([A, B, C], 30) == fingerprint.of([C, A, B], 30)


def test_a_message_added_removed_or_swapped_changes_it_and_so_does_a_newer_time():
    seen = {fingerprint.of(ids, ts) for ids, ts in
            [([A, B], 20), ([A, B, C], 20), ([A], 20), ([A, C], 20), ([A, B], 21)]}
    assert len(seen) == 5


def test_a_message_without_a_message_id_still_counts():
    assert fingerprint.of([A, None], 20) != fingerprint.of([A], 20)
    assert fingerprint.of([A, None]) == fingerprint.of([A, ""])


def test_the_forms_are_the_ones_drafts_ahead_were_stored_under():
    assert fingerprint.of([B, A], 20) == "t2:2:20:%08x" % zlib.crc32(f"{A}\n{B}".encode())
    assert fingerprint.message(A, 1) == f"m2:{A}:1"
    assert fingerprint.message(None) == "m2::0"


# ── migration 22 ─────────────────────────────────────────────────────────────

def at_v21(tmp_path, messages, summaries):
    """A version 21 database holding `messages` as (pk, uid, message_id, thread_id, ts) in Ada's
    INBOX, and `summaries` as (thread_id, fingerprint)."""
    path = str(tmp_path / "v21.db")
    migrate.init(path, migrations.MIGRATIONS[:21])
    c = sqlite3.connect(path)
    c.executemany("INSERT INTO messages(pk, account, kind, folder, uid, message_id, thread_id, ts) "
                  "VALUES (?, ?, 'imap', 'INBOX', ?, ?, ?, ?)", [(m[0], ADA, *m[1:]) for m in messages])
    c.executemany("INSERT INTO thread_summaries(account, thread_id, scope, fingerprint, tldr) "
                  "VALUES (?, ?, 'thread', ?, 'Lunch on Thursday.')", [(ADA, *s) for s in summaries])
    c.commit()
    c.close()
    return path


def stored(path):
    c = sqlite3.connect(path)
    try:
        return dict(c.execute("SELECT thread_id, fingerprint FROM thread_summaries"))
    finally:
        c.close()


def old(pks_and_ids):
    """The pk-based fingerprint a version 21 app stored: pk:Message-ID, oldest first, and the count."""
    import hashlib
    basis = "|".join(f"{pk}:{mid}" for pk, mid in pks_and_ids) + f"#{len(pks_and_ids)}"
    return hashlib.sha256(basis.encode()).hexdigest()[:32]


LUNCH = [(1, 1, A, "t-lunch", 100), (2, 2, B, "t-lunch", 200), (3, 3, B, "t-lunch", 200)]   # B twice
QUILT = [(4, 4, C, "t-quilt", 300)]


def test_a_fresh_summary_stays_fresh_and_a_stale_one_stale(tmp_path):
    path = at_v21(tmp_path, LUNCH + QUILT,
                  [("t-lunch", old([(1, A), (2, B)])),            # matches the thread as it is now
                   ("t-quilt", old([(4, C), (5, A)])),            # made when the thread held more
                   ("t-gone", "anything")])                       # a thread with no messages left
    migrate.init(path)
    assert stored(path) == {"t-lunch": fingerprint.of([A, B], 200), "t-quilt": old([(4, C), (5, A)]),
                            "t-gone": "anything"}


def test_migration_22_run_again_changes_nothing(tmp_path):
    path = at_v21(tmp_path, LUNCH, [("t-lunch", old([(1, A), (2, B)]))])
    migrate.init(path)
    once = stored(path)
    c = sqlite3.connect(path)
    try:
        m22.migrate(c)
        c.commit()
    finally:
        c.close()
    assert stored(path) == once


def test_the_migrated_fingerprint_is_the_one_the_app_computes(data_dir):
    from mailmind.bootstrap.mailbox import store
    import summarize_thread
    path = str(data_dir / "mailmind.db")
    c = sqlite3.connect(path)
    c.executemany("INSERT INTO messages(pk, account, kind, folder, uid, message_id, thread_id, ts) "
                  "VALUES (?, ?, 'imap', 'INBOX', ?, ?, ?, ?)", [(m[0], ADA, *m[1:]) for m in LUNCH])
    c.commit()
    c.close()
    c = store.conn()
    try:
        rows = summarize_thread._rows(c, ADA, "t-lunch")
    finally:
        c.close()
    raw = sqlite3.connect(path)
    try:
        frozen = m22._by_message_id(m22._rows(raw, ADA, "t-lunch"))
    finally:
        raw.close()
    assert summarize_thread._fingerprint(rows) == frozen == fingerprint.of([A, B], 200)
