"""The one path a change takes (mailmind.mailbox.application.commands.MailCommands), over the store and its queue in a temp
database, with a stand-in for what a try asks of the server (ServerOps.apply): what the message and Undo look like as a try
starts, and what a try that raises leaves. The path through the fake IMAP server, a crash, a busy gate and Undo are the
integration half's."""
from types import SimpleNamespace

import pytest

from mailmind.bootstrap import mailbox
from mailmind.bootstrap.mailbox import store
from mailmind.mailbox.application.commands import MailCommands
from mailmind.mailbox.domain import command
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.undo import Undo

ADA = "ada@example.test"


class _Accounts:
    def get(self, email):
        return {"email": email}

    def trash_folder(self, acct):
        return "Trash"

    def profile(self, acct):
        return SimpleNamespace(archive_is_label_removal=False)


class _Server:
    """ServerOps.apply: each try as it was handed, and what `looks` saw as it was asked. `raises` makes a try raise."""

    def __init__(self, answer=command.ServerAnswer(dest="Archive"), looks=None):
        self.answer, self.looks, self.raises = answer, looks, None
        self.tries = []

    def apply(self, row, first_try=False, timeout=None):
        self.tries.append({"id": row["id"], "first_try": first_try, "saw": self.looks() if self.looks else None})
        if self.raises:
            raise self.raises
        return self.answer


def a_message():
    """Ada's receipt in her Inbox, unread and unstarred."""
    with UnitOfWork().write() as c:
        store.set_uidvalidity(c, ADA, "INBOX", 1_700_000_001)
        return store.upsert_message(c, {"account": ADA, "kind": "imap", "folder": "INBOX", "uid": 5,
                                        "message_id": "<receipt-1042@example.test>", "gmail_thread": None,
                                        "from_addr": "shop@example.test", "from_name": "Shop", "subject": "Receipt 1042",
                                        "date_iso": None, "ts": 1, "snippet": "", "bucket": "receipt", "is_junk": 0})


def message(pk):
    with UnitOfWork().read() as c:
        return store.get_message(c, pk)


def rows():
    with UnitOfWork().read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM mail_commands ORDER BY id")]


def test_while_the_server_is_asked_the_message_is_hidden_and_undo_already_knows_it(data_dir):
    pk = a_message()
    undo = Undo(UnitOfWork(), {}, lambda: None, str)
    server = _Server(looks=lambda: (message(pk)["pending_removal"] is not None, [e["command"] for e in undo.entries()]))
    commands = MailCommands(UnitOfWork(), store, _Accounts(), server)
    accepted = commands.accept_move("trash", pk, "m-1", lambda c, entry: undo.push_within(c, entry))
    assert commands.submit(accepted) == command.APPLIED
    assert server.tries == [{"id": accepted.id, "first_try": True, "saw": (True, [accepted.id])}]


def test_a_flag_change_shows_before_the_server_is_asked(data_dir):
    pk = a_message()
    server = _Server(answer=command.ServerAnswer(), looks=lambda: message(pk)["flagged"])
    commands = MailCommands(UnitOfWork(), store, _Accounts(), server)
    assert commands.submit(commands.accept_flags(pk, {"flagged": True}, "f-1")) == command.APPLIED
    assert [t["saw"] for t in server.tries] == [1] and message(pk)["flagged"] == 1
    assert [(r["kind"], r["state"]) for r in rows()] == [("flag", "done")]


def test_a_click_whose_try_raises_something_unforeseen_leaves_it_to_the_worker(data_dir):
    pk = a_message()
    server = _Server()
    server.raises = ValueError("a bug")
    commands = MailCommands(UnitOfWork(), store, _Accounts(), server)
    with pytest.raises(ValueError):
        commands.submit(commands.accept_move("archive", pk, "m-1"))
    [row] = rows()
    assert (row["state"], row["attempts"]) == ("queued", 0)
    server.raises = None
    commands.drain()
    assert (rows()[0]["state"], message(pk)) == ("done", None)


def test_a_try_that_raises_something_unforeseen_doesnt_leave_its_message_hidden_for_good(data_dir):
    pk = a_message()
    server = _Server()
    server.raises = OSError("the connection went")
    commands = MailCommands(UnitOfWork(), store, _Accounts(), server)
    assert commands.submit(commands.accept_move("archive", pk, "m-1")) == command.QUEUED
    server.raises = ValueError("a bug")
    commands.drain()
    [row] = rows()
    assert (row["state"], row["attempts"], row["last_error"]) == ("queued", 1, "ValueError")


def test_a_queued_change_is_given_up_on_after_about_a_day_of_tries():
    hours = sum(mailbox.commands.QUEUE.backoff(n) for n in range(1, mailbox.commands.MAX_ATTEMPTS)) / 3600
    assert 23 < hours < 25
