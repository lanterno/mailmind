"""The database holds every message, so only its owner may read it, whatever the umask, and its log and index with it."""
import os
import sqlite3
import stat

import pytest

from mailmind.platform.db import database, migrate


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture(autouse=True)
def open_umask():
    was = os.umask(0o022)
    yield
    os.umask(was)


def test_a_new_database_is_its_owners_alone_and_so_is_its_log(tmp_path):
    path = str(tmp_path / "mailmind.db")
    c = database.connect(path)
    c.execute("CREATE TABLE t(x)")
    c.execute("INSERT INTO t VALUES (1)")
    c.commit()
    assert [mode(path + end) for end in ("", "-wal", "-shm")] == [0o600] * 3
    c.close()


def test_a_start_takes_back_a_database_from_before_with_its_log(tmp_path):
    path = str(tmp_path / "mailmind.db")
    c = sqlite3.connect(path)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE t(x)")
    c.execute("INSERT INTO t VALUES (1)")
    c.commit()
    for end in ("", "-wal", "-shm"):
        os.chmod(path + end, 0o644)
    migrate.init(path, steps=[])
    assert [mode(path + end) for end in ("", "-wal", "-shm")] == [0o600] * 3
    assert c.execute("SELECT x FROM t").fetchall() == [(1,)]
    c.close()


def test_a_connection_leaves_what_a_database_holds_and_its_mode_alone(tmp_path):
    path = str(tmp_path / "mailmind.db")
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE t(x)")
    c.execute("INSERT INTO t VALUES (1)")
    c.commit()
    c.close()
    os.chmod(path, 0o640)
    c = database.connect(path)
    assert [r["x"] for r in c.execute("SELECT x FROM t")] == [1]
    assert mode(path) == 0o640
    c.close()


def test_a_folder_that_isnt_there_is_still_for_sqlite_to_say(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        database.connect(str(tmp_path / "nowhere" / "mailmind.db"))


def test_a_database_that_isnt_there_yet_costs_a_start_nothing(tmp_path):
    database.keep_private(str(tmp_path / "mailmind.db"))
    assert not (tmp_path / "mailmind.db").exists()
