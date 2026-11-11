"""`python -m mailmind db compact`: it gives the room back, and it stops short rather than half do it."""
import os
import shutil
from collections import namedtuple

import pytest

from mailmind.platform.db import compact, database, migrate

MB = 1_000_000


def report_of(path):
    lines = []
    return compact.compact(path, say=lines.append), "\n".join(lines)


@pytest.fixture
def freed(tmp_path):
    """A database in which a large table was emptied, so its file holds room it isn't using."""
    path = str(tmp_path / "mailmind.db")
    migrate.init(path)
    c = database.keyed(path)
    c.execute("CREATE TABLE ballast(x BLOB)")
    c.executemany("INSERT INTO ballast VALUES (?)", [(b"x" * 100_000,) for _ in range(40)])
    c.execute("INSERT INTO muted(account, thread_id, muted_at) VALUES ('ada@example.test', 't1', 'now')")
    c.commit()
    c.execute("DELETE FROM ballast")
    c.commit()
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    c.close()
    return path


def test_it_gives_the_room_back_and_keeps_the_rows(freed):
    size = os.path.getsize(freed)
    code, report = report_of(freed)
    assert code == compact.OK, report
    assert os.path.getsize(freed) < size - 3 * MB
    assert report.startswith("The database went from ") and "ada@example.test" not in report
    c = database.keyed(freed)
    assert c.execute("SELECT thread_id FROM muted").fetchall() == [("t1",)]
    assert c.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    assert c.execute("PRAGMA user_version").fetchone()[0] == migrate.migrations.MIGRATIONS[-1][0]
    c.close()


def test_a_database_in_use_is_left_as_it_was(freed, monkeypatch):
    monkeypatch.setattr(compact, "WAIT_SECONDS", 0)
    size = os.path.getsize(freed)
    other = database.keyed(freed)
    other.execute("BEGIN IMMEDIATE")
    code, report = report_of(freed)
    other.rollback()
    other.close()
    assert (code, os.path.getsize(freed) >= size) == (compact.PROBLEM, True)
    assert "in use" in report


def test_too_little_room_on_the_disk_stops_it_before_it_starts(freed, monkeypatch):
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: usage(MB, MB, 1))
    size = os.path.getsize(freed)
    code, report = report_of(freed)
    assert (code, os.path.getsize(freed) >= size) == (compact.PROBLEM, True)
    assert "free on the disk" in report and "Nothing changed" in report


def test_no_database_is_said_plainly(tmp_path):
    code, report = report_of(str(tmp_path / "none.db"))
    assert code == compact.CANNOT_RUN and report.startswith("No database at ")
