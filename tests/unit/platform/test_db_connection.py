"""mailmind.platform.db.database.connect: how every connection to the database is set up."""
import pytest

from mailmind.platform.db import database


@pytest.fixture
def c(tmp_path):
    c = database.connect(str(tmp_path / "connected.db"))
    yield c
    c.close()


def pragma(c, name):
    return c.execute(f"PRAGMA {name}").fetchone()[0]


def test_it_logs_ahead_waits_for_a_lock_and_enforces_foreign_keys(c):
    """Deleting a message deletes its body, attachments and summary only because foreign keys
    are on; they are off in SQLite unless each connection turns them on."""
    assert pragma(c, "journal_mode") == "wal"
    assert pragma(c, "busy_timeout") == 60000
    assert pragma(c, "foreign_keys") == 1
    c.executescript("CREATE TABLE parent(pk INTEGER PRIMARY KEY);"
                    "CREATE TABLE child(pk INTEGER REFERENCES parent(pk) ON DELETE CASCADE);"
                    "INSERT INTO parent VALUES (1); INSERT INTO child VALUES (1);"
                    "DELETE FROM parent;")
    assert c.execute("SELECT count(*) AS n FROM child").fetchone()["n"] == 0      # a row, by name


def test_a_commit_is_on_the_disk_itself_before_it_returns(c):
    """The outbox's `delivered` and the queue's `applied` must survive a power cut, or something is
    sent or applied twice: the log is synced at every commit (FULL is 2), past the drive's cache
    on macOS."""
    assert pragma(c, "synchronous") == 2
    assert pragma(c, "fullfsync") == 1


def test_what_a_big_sort_holds_of_the_mail_stays_in_memory(tmp_path):
    c = database.connect(str(tmp_path / "m.db"))
    try:
        assert c.execute("PRAGMA temp_store").fetchone()[0] == 2     # MEMORY
    finally:
        c.close()
