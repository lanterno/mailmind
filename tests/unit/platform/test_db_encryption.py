"""The database on disk (mailmind.platform.db.database): encrypted, with its log, its backups and its export; a plaintext
file refused and left as it was; a key that doesn't fit opening nothing. Every file here is a test's own, in a temp
directory, and every key the test key or one made here."""
import hashlib
import os
import sqlite3

import pytest

from mailmind.platform import environment, settings
from mailmind.platform.db import backup, database, driver, export, migrate, migrations
from mailmind.platform.db.migrations import m0001_m0020

MARK = "linen-swatch-4417"              # written into every database here; no file may hold it as written


def holds_mark(folder):
    """Whether any file in `folder`, at any depth, holds MARK as it was written."""
    return [p.name for p in folder.rglob("*") if p.is_file() and MARK.encode() in p.read_bytes()]


def write_mark(path):
    c = database.connect(path)
    c.execute("INSERT INTO messages(account, kind, folder, uid, message_id, subject, from_addr) "
              "VALUES ('ada@example.test', 'imap', 'INBOX', 1, '<1@example.test>', ?, 'lin@example.test')", (MARK,))
    c.execute("INSERT INTO bodies(message_pk, raw) VALUES (1, ?)", (MARK.encode() * 20,))
    c.commit()
    return c


def fingerprint(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def test_a_new_database_its_log_and_its_index_hold_nothing_as_written(data_dir):
    c = write_mark(settings.current().db)                  # still open: what was written is in the log too
    try:
        assert os.path.getsize(settings.current().db + "-wal") > 0
        assert holds_mark(data_dir) == []
        assert not database.plaintext(settings.current().db)
        assert c.execute("SELECT subject FROM messages").fetchone()[0] == MARK
    finally:
        c.close()


def test_the_standard_library_reads_nothing_of_it(data_dir):
    write_mark(settings.current().db).close()
    with pytest.raises(sqlite3.DatabaseError, match="not a database"):
        sqlite3.connect(settings.current().db).execute("SELECT count(*) FROM sqlite_master").fetchone()


def test_another_key_opens_nothing_and_says_so(data_dir, monkeypatch):
    write_mark(settings.current().db).close()
    monkeypatch.setenv(environment.DB_KEY.name, "00" * 32)
    with pytest.raises(database.KeyDoesNotFit):
        database.connect(settings.current().db)


def test_a_plaintext_database_is_refused_and_left_as_it_was(tmp_path):
    path = str(tmp_path / "mailmind.db")
    plain = sqlite3.connect(path)
    plain.execute("CREATE TABLE messages(subject TEXT)")
    plain.execute("INSERT INTO messages VALUES (?)", (MARK,))
    plain.commit()
    plain.close()
    before = fingerprint(path)
    with settings.use(settings.Settings(home=str(tmp_path))):
        for opening in (lambda: database.connect(path), lambda: migrate.init(path),
                        lambda: database.keyed(path, read_only=True)):
            with pytest.raises(database.NotEncrypted, match="db encrypt --dry"):
                opening()
    assert fingerprint(path) == before and sorted(os.listdir(tmp_path)) == ["mailmind.db"]


def test_a_backup_is_encrypted_as_the_database_is(data_dir):
    c = write_mark(settings.current().db)
    try:
        copy = backup.take(c, 1)
    finally:
        c.close()
    assert not database.plaintext(copy) and holds_mark(data_dir) == []
    opened = database.keyed(copy)
    try:
        assert opened.execute("SELECT subject FROM messages").fetchone()[0] == MARK
    finally:
        opened.close()


def test_a_database_export_is_encrypted_as_the_database_is(data_dir, tmp_path_factory):
    write_mark(settings.current().db).close()
    out = tmp_path_factory.mktemp("export")
    path = export.export_db(str(out))
    assert not database.plaintext(path) and holds_mark(out) == []
    opened = database.keyed(path)
    try:
        assert opened.execute("SELECT subject FROM messages").fetchone()[0] == MARK
    finally:
        opened.close()


def test_the_frozen_migrations_catch_what_the_driver_raises(data_dir):
    """Their `except sqlite3.OperationalError` was written for the standard library's class: it must catch the driver's."""
    assert m0001_m0020.sqlite3 is driver
    c = database.connect(settings.current().db)
    try:
        migrations.add_column(c, "messages", "subject TEXT")    # there already: the driver's error, caught
    finally:
        c.close()
