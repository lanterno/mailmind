"""Migration 50: a row naming by its pk a message that is gone goes, in every table that goes with its message, as the
message's delete would have taken it; a row naming a message still there stays. Each database here is fictional, built at
its version in a temp folder."""
from mailmind.platform.db import database, migrate, migrations
from mailmind.platform.db.migrations import m0050_rows_of_mail_gone as m50

ADA = "ada@example.test"
MARK = "quilt-pattern-7731"


def at(tmp_path, version):
    path = str(tmp_path / "mailmind.db")
    migrate.init(path, migrations.MIGRATIONS[:version])
    return path


def rows(path, sql):
    c = database.keyed(path)
    try:
        return c.execute(sql).fetchall()
    finally:
        c.close()


def with_one_message_and_rows_of_two_gone(path):
    c = database.keyed(path)                   # foreign keys aren't enforced here, as an older MailMind left them
    kept = c.execute("INSERT INTO messages(account, kind, folder, uid, subject) VALUES (?, 'imap', 'INBOX', 1, ?)",
                     (ADA, MARK)).lastrowid
    for pk in (kept, 98, 99):
        c.execute("INSERT INTO bodies(message_pk, raw) VALUES (?, ?)", (pk, MARK.encode()))
        c.execute("INSERT INTO attachments(message_pk, part_id, filename, content_type) VALUES (?, '2', 'f', 'application/pdf')",
                  (pk,))
    c.commit()
    c.close()
    return kept


def test_the_rows_of_a_message_gone_go_and_those_of_one_there_stay(tmp_path):
    path = at(tmp_path, 49)
    kept = with_one_message_and_rows_of_two_gone(path)
    migrate.init(path)
    assert rows(path, "SELECT message_pk FROM bodies") == [(kept,)]
    assert rows(path, "SELECT message_pk FROM attachments") == [(kept,)]
    assert rows(path, "PRAGMA foreign_key_check") == []


def test_every_table_that_goes_with_its_message_is_covered(tmp_path):
    """Each table naming a message by its pk with ON DELETE CASCADE, as the schema has them now: a new one is covered
    without a change here."""
    path = at(tmp_path, 49)
    c = database.keyed(path)
    try:
        covered = dict(m50._cascading_from_messages(c))
    finally:
        c.close()
    assert {"bodies", "attachments", "message_labels"} <= set(covered)


def test_a_database_with_nothing_gone_is_left_as_it_was(tmp_path):
    path = at(tmp_path, 49)
    c = database.keyed(path)
    pk = c.execute("INSERT INTO messages(account, kind, folder, uid) VALUES (?, 'imap', 'INBOX', 1)", (ADA,)).lastrowid
    c.execute("INSERT INTO bodies(message_pk, raw) VALUES (?, ?)", (pk, MARK.encode()))
    c.commit()
    c.close()
    migrate.init(path)
    assert rows(path, "SELECT message_pk FROM bodies") == [(pk,)]
