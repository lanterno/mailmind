"""Who a message went to, one row each: what `/api/contacts/known` reads, how ingest and opening a message
write it, and what migration 35 made of the text column for mail already stored."""
import sqlite3

import pytest



# -- what migration 35 made of mail already stored ---------------------------

@pytest.fixture
def stored(tmp_path):
    from mailmind.platform.db.migrations import m0035_message_recipients as m
    db = sqlite3.connect(tmp_path / "old.db")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("CREATE TABLE messages(pk INTEGER PRIMARY KEY, recipients TEXT)")
    db.execute("CREATE TABLE bodies(message_pk INTEGER PRIMARY KEY, raw BLOB)")

    def run(*texts, raw=None):
        db.executemany("INSERT INTO messages(recipients) VALUES (?)", [(t,) for t in texts])
        if raw is not None:
            db.execute("INSERT INTO bodies(message_pk, raw) VALUES ((SELECT MAX(pk) FROM messages), ?)", (raw,))
        m.migrate(db)
        return [r[0] for r in db.execute("SELECT addr FROM message_recipients ORDER BY addr")]
    yield run
    db.close()


def test_stored_mail_is_read_from_the_text_column(stored):
    assert stored('Lio Park <lio@example.test>, "Smith, Cy" <Cy@Example.Test> kim@example.test') == [
        "cy@example.test", "kim@example.test", "lio@example.test"]


def test_a_quoted_name_in_stored_mail_is_no_address(stored):
    assert stored('"ann@example.test via List" <list@example.test>') == ["list@example.test"]


def test_the_last_token_of_a_column_that_was_cut_is_left_out(stored):
    head = "first@example.test, second@example.test, "
    cut = head + "x" * (2000 - len(head) - len("zed@example.te") - 1) + " zed@example.te"
    assert len(cut) == 2000
    assert stored(cut) == ["first@example.test", "second@example.test"]


def _cut():
    head = "first@example.test, second@example.test, "
    return head + "x" * (2000 - len(head) - len("zed@example.te") - 1) + " zed@example.te"


def test_a_column_that_was_cut_is_read_from_the_stored_message(stored):
    raw = (b"From: Ada <ada@example.test>\r\nTo: first@example.test,\r\n second@example.test\r\n"
           b"Cc: =?utf-8?Q?ann=40example.test?= <zed@example.test>\r\nSubject: x\r\n\r\nBody.\r\n")
    assert stored(_cut(), raw=raw) == ["first@example.test", "second@example.test", "zed@example.test"]


def test_a_stored_message_that_says_nothing_leaves_the_text_to_say(stored):
    assert stored(_cut(), raw=b"not really a message") == ["first@example.test", "second@example.test"]


def test_a_column_that_wasnt_cut_is_read_from_the_text_alone(stored):
    assert stored("a@example.test", raw=b"To: b@example.test\r\n\r\nBody.\r\n") == ["a@example.test"]


def test_a_column_shorter_than_the_cut_keeps_its_last_token(stored):
    assert stored("first@example.test zed@example.test") == ["first@example.test", "zed@example.test"]


def test_stored_mail_with_nothing_there_adds_nothing(stored):
    assert stored("", None) == []


def test_the_migration_can_run_again(stored):
    assert stored("a@example.test") == ["a@example.test"]
    assert stored() == ["a@example.test"]
