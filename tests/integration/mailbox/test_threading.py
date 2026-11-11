"""Threading what a sync stored (store.thread_stored, the Threader in the Mailbox's domain): what it decides, what it
reads and rewrites to do so, and that no sync takes the whole-account path. test_threads.py pins the whole-account path,
and test_threading_property.py holds the two to the same threads."""
import sqlite3

import pytest

from fixtures import world as w


# -- through a sync ------------------------------------------------------------

@pytest.fixture
def thread_of(world):
    def of(mid, account=w.ADA):
        return world_rows(account, mid)[0]["thread_id"]
    return of


def world_rows(account, mid):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return [dict(r) for r in c.execute("SELECT * FROM messages WHERE account=? AND message_id=?", (account, mid))]
    finally:
        c.close()


def reply_to(parent, mid):
    return (f"From: Lin <lin@example.test>\r\nTo: {w.ADA}\r\nSubject: Re: Hello\r\nDate: Mon, 02 Nov 2026 09:00:00 +0000\r\n"
            f"Message-ID: {mid}\r\nIn-Reply-To: {parent}\r\nReferences: {parent}\r\n\r\nThursday works.\r\n").encode()


def test_no_sync_regroups_the_whole_account(world, thread_of, monkeypatch):
    from mailmind.bootstrap.mailbox import store

    def refuses(c, account):
        raise AssertionError("a sync took the whole-account path")
    monkeypatch.setattr(store, "recompute_threads", refuses)
    parent = w.message_id("ada-inbox-01-lunch.eml")
    world.deliver(w.ADA, "INBOX", reply_to(parent, "<late-reply@example.test>"))
    assert thread_of("<late-reply@example.test>") == thread_of(parent)


def test_a_sync_hands_threading_only_what_it_stored(world, monkeypatch):
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.mailbox import ingest, store
    real, heard = store.thread_stored, []

    def spy(c, account, pks=()):
        heard.append(list(pks))
        return real(c, account, pks)
    monkeypatch.setattr(store, "thread_stored", spy)
    ingest.sync_folder(accounts.get(w.ADA), "INBOX")               # nothing new
    world.deliver(w.ADA, "INBOX", reply_to(w.message_id("ada-inbox-01-lunch.eml"), "<late-reply@example.test>"))
    pk = world_rows(w.ADA, "<late-reply@example.test>")[0]["pk"]
    assert heard == [[], [pk]]
    heard.clear()
    ingest.sync_folder(accounts.get(w.ADA), "INBOX", full=True)    # every UID stored again, and nothing to thread
    assert heard == [[]]


def test_mail_a_sync_stored_and_never_threaded_goes_with_the_next_one(world, thread_of, monkeypatch):
    from mailmind.bootstrap.mailbox import store
    real = store.thread_stored

    def dies(c, account, pks=()):
        raise OSError("the power went")
    parent = w.message_id("ada-inbox-01-lunch.eml")
    monkeypatch.setattr(store, "thread_stored", dies)
    with pytest.raises(OSError):
        world.deliver(w.ADA, "INBOX", reply_to(parent, "<first@example.test>"))
    assert world_rows(w.ADA, "<first@example.test>")[0]["thread_id"] is None
    monkeypatch.setattr(store, "thread_stored", real)
    world.deliver(w.ADA, "INBOX", reply_to("<first@example.test>", "<second@example.test>"))
    assert thread_of("<first@example.test>") == thread_of("<second@example.test>") == thread_of(parent)


def test_a_merge_that_fails_in_a_sync_holds_no_lock_and_the_next_sync_does_it(world, thread_of, monkeypatch):
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.mailbox import ingest
    from mailmind.mailbox.adapters.sqlite import bound
    a, b = w.message_id("ada-inbox-03-newsletter.eml"), w.message_id("ada-inbox-05-receipt.eml")
    assert thread_of(a) != thread_of(b)
    joined = (f"From: Lin <lin@example.test>\r\nTo: {w.ADA}\r\nSubject: Both\r\nDate: Mon, 02 Nov 2026 09:00:00 +0000\r\n"
              f"Message-ID: <both@example.test>\r\nReferences: {a} {b}\r\n\r\nBoth of them.\r\n").encode()
    real = bound._merged

    def fails(c, merged):
        raise sqlite3.OperationalError("disk full")
    monkeypatch.setattr(bound, "_merged", fails)
    with pytest.raises(sqlite3.OperationalError):
        world.deliver(w.ADA, "INBOX", joined)
    c = sqlite3.connect(world.dir / "mailmind.db", timeout=0)
    c.execute("UPDATE sync_state SET last_sync=last_sync")       # no waiting: the write lock is free
    c.commit()
    c.close()
    assert world_rows(w.ADA, "<both@example.test>")[0]["thread_id"] is None and thread_of(a) != thread_of(b)
    monkeypatch.setattr(bound, "_merged", real)
    ingest.sync_folder(accounts.get(w.ADA), "INBOX")
    assert thread_of("<both@example.test>") == thread_of(a) == thread_of(b)


def test_mail_the_command_line_stored_goes_with_the_next_sync(world, thread_of):
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.mailbox import ingest
    world.server.deliver(w.ADA, "INBOX", reply_to(w.message_id("ada-inbox-01-lunch.eml"), "<by-hand@example.test>"))
    ingest.ingest_account(accounts.get(w.ADA), 3650)               # `mailmind sync` threads nothing
    assert world_rows(w.ADA, "<by-hand@example.test>")[0]["thread_id"] is None
    ingest.sync_folder(accounts.get(w.ADA), "Archive")
    assert thread_of("<by-hand@example.test>") == thread_of(w.message_id("ada-inbox-01-lunch.eml"))


def test_undo_taking_a_message_back_threads_it(world, thread_of):
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.mailbox import ingest
    parent = w.message_id("ada-inbox-01-lunch.eml")
    world.server.deliver(w.ADA, "INBOX", reply_to(parent, "<undone@example.test>"))
    assert ingest.ingest_message(accounts.get(w.ADA), "INBOX", "<undone@example.test>") == 1
    assert thread_of("<undone@example.test>") == thread_of(parent)
