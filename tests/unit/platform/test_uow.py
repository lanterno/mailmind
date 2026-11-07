"""One connection for one block: what a read block gives, what a write block holds and when it lets go."""
import sqlite3

import pytest

from fixtures import world as w
from mailmind.platform import settings
from mailmind.platform.db import database, txguard
from mailmind.platform.db.txguard import TxAcrossNetworkError
from mailmind.platform.db.uow import NestedWrite, UnitOfWork, WroteInARead


@pytest.fixture
def uow(data_dir):
    c = database.connect(settings.current().db)
    c.execute("CREATE TABLE notes(id INTEGER PRIMARY KEY, what TEXT)")
    c.commit()
    c.close()
    return UnitOfWork()


def notes():
    c = database.connect(settings.current().db)
    try:
        return [r["what"] for r in c.execute("SELECT what FROM notes ORDER BY id")]
    finally:
        c.close()


def add(c, what="a note"):
    c.execute("INSERT INTO notes(what) VALUES(?)", (what,))


def closed(c):
    with pytest.raises(sqlite3.ProgrammingError):
        c.execute("SELECT 1")
    return True


def the_write_lock_is_free():
    other = sqlite3.connect(settings.current().db, timeout=0)
    try:
        other.execute("BEGIN IMMEDIATE")
        other.rollback()
        return True
    except sqlite3.OperationalError as e:
        assert "locked" in str(e)
        return False
    finally:
        other.close()


def test_a_read_block_reads_holds_no_transaction_and_closes(uow):
    with uow.write() as c:
        add(c)
    with uow.read() as c:
        assert [r["what"] for r in c.execute("SELECT what FROM notes")] == ["a note"]
        assert not c.in_transaction
        assert the_write_lock_is_free()
    assert closed(c)


def test_the_network_is_fine_from_a_read_block(uow):
    with uow.read():
        txguard.refuse_network("IMAP FETCH")


def test_a_read_block_closes_when_it_raises(uow):
    with pytest.raises(ValueError):
        with uow.read() as c:
            raise ValueError
    assert closed(c)


def test_a_write_in_a_read_block_raises_rather_than_being_lost_in_silence(uow):
    with pytest.raises(WroteInARead):
        with uow.read() as c:
            add(c)
    assert closed(c)
    assert notes() == []
    assert the_write_lock_is_free()


def test_a_read_blocks_own_error_is_the_one_heard(uow):
    with pytest.raises(ValueError):
        with uow.read() as c:
            add(c)
            raise ValueError


def test_a_write_block_that_ends_is_committed_and_closes(uow):
    with uow.write() as c:
        add(c)
    assert notes() == ["a note"]
    assert closed(c)
    assert the_write_lock_is_free()


def test_a_write_block_that_returns_early_keeps_what_it_wrote(uow):
    def early():
        with uow.write() as c:
            add(c)
            return c
    assert closed(early())
    assert notes() == ["a note"]


def test_a_write_block_that_raises_leaves_nothing_and_closes(uow):
    with pytest.raises(ValueError):
        with uow.write() as c:
            add(c)
            raise ValueError
    assert notes() == []
    assert closed(c)
    assert the_write_lock_is_free()


def test_the_write_lock_is_held_from_the_first_line_of_the_block(uow):
    assert the_write_lock_is_free()
    with uow.write() as c:
        assert c.in_transaction
        assert not the_write_lock_is_free()
    assert the_write_lock_is_free()


def test_a_write_block_that_writes_nothing_commits_nothing(uow):
    with uow.write() as c:
        assert c.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0
    assert notes() == []
    assert the_write_lock_is_free()


def test_a_write_block_sees_each_block_before_it_and_no_read_sees_it_until_it_commits(uow):
    with uow.write() as c:
        add(c, "first")
    with uow.write() as w:
        add(w, "second")
        with uow.read() as r:
            assert [x["what"] for x in r.execute("SELECT what FROM notes ORDER BY id")] == ["first"]
        assert [x["what"] for x in w.execute("SELECT what FROM notes ORDER BY id")] == ["first", "second"]
    assert notes() == ["first", "second"]


def test_a_commit_midway_through_a_block_stands_and_the_rest_commits_at_its_end(uow):
    """As an adapter that must commit before a network call does."""
    with uow.write() as c:
        add(c, "before")
        c.commit()
        assert the_write_lock_is_free()
        add(c, "after")
    assert notes() == ["before", "after"]


# ---- the network stays out of a write block ----

def test_any_network_check_inside_a_write_block_raises(uow):
    with uow.write():
        with pytest.raises(TxAcrossNetworkError, match="^IMAP FETCH while a transaction is open on this thread$"):
            txguard.refuse_network("IMAP FETCH")
    txguard.refuse_network("IMAP FETCH")


def test_an_imap_session_inside_a_write_block_raises_before_anything_is_sent(world):
    from mailmind.bootstrap import accounts, mailbox
    before = len(world.server.commands)
    with UnitOfWork().write():
        with pytest.raises(TxAcrossNetworkError, match="^an IMAP connection while"):
            mailbox.open_session(accounts.get(w.ADA))
    assert len(world.server.commands) == before


def test_an_smtp_session_inside_a_write_block_raises_before_anything_is_sent(world):
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.compose import transport
    before = len(world.smtp.commands)
    with UnitOfWork().write():
        with pytest.raises(TxAcrossNetworkError, match="^an SMTP connection while"):
            transport.send(accounts.get(w.ADA), b"Subject: x\r\n\r\nx\r\n", ["lin@example.test"])
    assert len(world.smtp.commands) == before and world.smtp.messages == []


def test_a_claude_request_inside_a_write_block_raises_before_anything_is_sent(world, ai, monkeypatch):
    from mailmind.bootstrap import assistant
    monkeypatch.setattr(assistant.status, "_last", {"ok": None, "ts": 0, "error": ""})   # report() stays as it was
    with UnitOfWork().write():
        with pytest.raises(TxAcrossNetworkError, match="^a Claude API request while"):
            assistant.calls.call_claude("Say hello to Ada.")
    assert ai.requests == []


def test_a_network_call_refused_inside_a_write_block_undoes_the_block_if_it_goes_uncaught(uow):
    with pytest.raises(TxAcrossNetworkError):
        with uow.write() as c:
            add(c)
            txguard.refuse_network("IMAP APPEND")
    assert notes() == []


# ---- a write block can't wait for itself ----

def test_a_write_block_inside_a_write_block_raises_at_once_and_the_outer_one_stands(uow):
    with uow.write() as c:
        add(c, "outer")
        with pytest.raises(NestedWrite):
            with uow.write():
                raise AssertionError("never opened")
    assert notes() == ["outer"]


def test_a_write_block_inside_another_connections_open_transaction_raises_at_once(uow):
    c = database.connect(settings.current().db)
    try:
        add(c, "by hand")
        with pytest.raises(NestedWrite):
            with uow.write():
                raise AssertionError("never opened")
    finally:
        c.close()


def test_a_write_block_inside_a_read_block_is_fine(uow):
    """A read block holds no transaction, so a later write needn't wait for it."""
    with uow.read() as r:
        with uow.write() as w:
            add(w)
        assert r.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1
    assert notes() == ["a note"]


# ---- which connection ----

@pytest.mark.parametrize("block", ["read", "write"])
def test_each_block_goes_through_database_connect(uow, block, monkeypatch):
    opened = []
    real = database.connect

    def connect(path):
        opened.append(path)
        return real(path)
    monkeypatch.setattr(database, "connect", connect)
    with getattr(uow, block)() as c:
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert c.execute("PRAGMA synchronous").fetchone()[0] == 2     # FULL
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert opened == [settings.current().db]


def test_a_block_finds_the_data_directory_as_it_is_when_it_opens(uow, tmp_path):
    home = tmp_path / "another"
    home.mkdir()
    with settings.use(settings.Settings(home=str(home))):
        with uow.write() as c:
            c.execute("CREATE TABLE elsewhere(id INTEGER)")
        assert (home / "mailmind.db").exists()
    with uow.read() as c:
        assert c.execute("SELECT 1 FROM sqlite_master WHERE name='elsewhere'").fetchone() is None


def test_a_block_can_be_on_any_database(tmp_path):
    uow = UnitOfWork(lambda: database.connect(str(tmp_path / "other.db")))
    with uow.write() as c:
        c.execute("CREATE TABLE t(id INTEGER)")
        c.execute("INSERT INTO t VALUES(1)")
    with uow.read() as c:
        assert c.execute("SELECT id FROM t").fetchone()[0] == 1
