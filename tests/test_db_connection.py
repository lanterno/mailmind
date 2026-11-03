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


def test_a_commit_is_on_the_disk_itself_before_it_returns(c):
    """The outbox's `delivered` and the queue's `applied` must survive a power cut, or something is
    sent or applied twice: the log is synced at every commit (FULL is 2), past the drive's cache
    on macOS."""
    assert pragma(c, "synchronous") == 2
    assert pragma(c, "fullfsync") == 1
