"""A message's key: what makes rows copies of one message, what it keeps through a move, an Undo, a
renumber and a second folder, how long it waits for a copy to come back, and where the API shows it."""

from mailmind.mailbox.domain import identity
from mailmind.platform.db import database, migrate, migrations


def execute(sql, *args):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        c.execute(sql, args)
        c.commit()
    finally:
        c.close()


# -- what a key is ------------------------------------------------------------------------------------

def test_the_identity_is_the_message_id_without_the_whitespace_a_fold_left():
    assert identity.of("<a@example.test>", "x", "y", "z") == "<a@example.test>"
    assert identity.of(" <a@\r\n example.test>\n", "x", "y", "z") == "<a@example.test>"


def test_a_message_with_no_id_is_known_by_its_envelope():
    one = identity.of("", "bo@example.test", "2026-11-02T10:00:00+00:00", "Hello")
    assert one == identity.of(None, "bo@example.test", "2026-11-02T10:00:00+00:00", "Hello")
    assert one.startswith("~") and one != identity.of("", "bo@example.test", "2026-11-02T10:00:00+00:00", "Goodbye")


def test_migration_30_gives_the_keys_and_keeps_what_the_ai_said(tmp_path):
    path = str(tmp_path / "old.db")
    migrate.init(path, migrations.MIGRATIONS[:29])
    c = database.keyed(path)
    rows = [(1, "INBOX", 1, "<a@example.test>"), (2, "Archive", 7, "<a@example.test>"), (3, "INBOX", 2, ""),
            (4, "INBOX", 3, None)]
    c.executemany("INSERT INTO messages(pk, account, kind, folder, uid, message_id, from_addr, subject, date_iso) "
                  "VALUES (?, 'ada@example.test', 'imap', ?, ?, ?, 'bo@example.test', 'Hello', '2026-11-02')", rows)
    c.execute("INSERT INTO summaries(message_pk, summary, importance, created_at) VALUES (2, 'Said once', 3, 't')")
    c.commit()
    c.close()
    migrate.init(path)
    c = database.keyed(path)
    try:
        keys = dict(c.execute("SELECT pk, message_key FROM messages"))
        assert keys[1] == keys[2] is not None
        assert keys[3] == keys[4] is not None and keys[3] != keys[1]     # the same envelope, no id
        assert c.execute("SELECT message_key, summary FROM assessments").fetchall() == [(keys[1], "Said once")]
    finally:
        c.close()


def test_migration_30_can_run_again(tmp_path):
    path = str(tmp_path / "old.db")
    migrate.init(path, migrations.MIGRATIONS[:30])
    c = database.keyed(path)
    c.execute("PRAGMA user_version=29")
    c.commit()
    c.close()
    migrate.init(path, migrations.MIGRATIONS[:30])
    c = database.keyed(path)
    try:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 30
    finally:
        c.close()
