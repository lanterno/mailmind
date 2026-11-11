"""mailmind.platform.db.txguard: no transaction stays open across a network call. database.connect
tracks each connection for the thread that opened it, and refuse_network raises while one of the
thread's connections has a transaction open."""
import gc
import threading

import pytest

from mailmind.platform.db import database, driver, txguard
from mailmind.platform.db.txguard import TxAcrossNetworkError


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "guard.db")
    c = database.connect(path)
    c.execute("CREATE TABLE notes(id INTEGER PRIMARY KEY, body TEXT)")
    c.commit()
    c.close()
    return path


def test_an_uncommitted_write_refuses_the_network_until_it_commits(db):
    c = database.connect(db)
    c.execute("INSERT INTO notes(body) VALUES ('lunch with Lin')")
    with pytest.raises(TxAcrossNetworkError, match="^IMAP FETCH while a transaction is open on this thread$"):
        txguard.refuse_network("IMAP FETCH")
    c.commit()
    txguard.refuse_network("IMAP FETCH")
    c.close()


def test_a_rolled_back_write_lets_the_network_through(db):
    c = database.connect(db)
    c.execute("UPDATE notes SET body = 'x'")
    c.rollback()
    txguard.refuse_network("SMTP")
    c.close()


def test_a_read_opens_no_transaction(db):
    c = database.connect(db)
    c.execute("SELECT * FROM notes").fetchall()
    txguard.refuse_network("a Claude API request")
    c.close()


def test_a_connection_closed_or_dropped_mid_transaction_no_longer_counts(db):
    c = database.connect(db)
    c.execute("INSERT INTO notes(body) VALUES ('x')")
    c.close()
    assert txguard.open_transactions() == 0
    d = database.connect(db)
    d.execute("INSERT INTO notes(body) VALUES ('y')")
    assert txguard.open_transactions() == 1
    del d
    gc.collect()
    assert txguard.open_transactions() == 0


def test_another_threads_transaction_is_not_in_this_ones_way(db):
    opened, done = threading.Event(), threading.Event()

    def writer():
        c = database.connect(db)
        c.execute("INSERT INTO notes(body) VALUES ('held')")
        opened.set()
        done.wait(5)
        c.rollback()
        c.close()
    t = threading.Thread(target=writer)
    t.start()
    try:
        assert opened.wait(5)
        txguard.refuse_network("IMAP NOOP")
    finally:
        done.set()
        t.join(5)


def test_every_open_transaction_is_counted(db):
    a, b = database.connect(db), database.connect(db)
    a.execute("INSERT INTO notes(body) VALUES ('a')")
    a.commit()
    b.execute("SELECT 1")
    assert txguard.open_transactions() == 0
    a.execute("DELETE FROM notes")
    assert txguard.open_transactions() == 1
    a.rollback()
    a.close()
    b.close()


def test_guard_calls_checks_before_each_call_and_names_it():
    class Session:
        def __init__(self):
            self.sent = []

        def send(self, line):
            self.sent.append(line)
            return len(line)
    s = txguard.guard_calls(Session(), "send", "SMTP")
    assert s.send("NOOP") == 4 and s.sent == ["NOOP"]

    class Imap:
        def _command(self, name, *args):
            return name
    m = txguard.guard_calls(Imap(), "_command", lambda name, *args: f"IMAP {name}")
    assert m._command("NOOP") == "NOOP"


def test_guard_calls_refuses_before_the_call_reaches_the_object(db):
    sent = []

    class Session:
        def send(self, line):
            sent.append(line)
    s = txguard.guard_calls(Session(), "send", "SMTP")
    c = database.connect(db)
    c.execute("INSERT INTO notes(body) VALUES ('x')")
    with pytest.raises(TxAcrossNetworkError, match="^SMTP while"):
        s.send("MAIL FROM:<ada@example.test>")
    assert sent == []
    c.rollback()
    c.close()


def test_guard_calls_leaves_an_object_without_that_method_alone():
    class StandIn:
        pass
    obj = StandIn()
    assert txguard.guard_calls(obj, "send", "SMTP") is obj
    assert not hasattr(obj, "send")


def test_each_connection_is_still_an_sqlite3_connection(db):
    c = database.connect(db)
    assert isinstance(c, driver.Connection)
    c.close()
