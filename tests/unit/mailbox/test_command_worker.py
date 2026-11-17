"""The command queue in a read-only process (mailmind.mailbox.application.commands.MailCommands), over the store and its
queue in a temp database: it refuses a change before anything is queued, and its worker drains through MailCommands.drain,
which leaves what a full run queued for the next. What a crash left running, put back and applied through the fake server,
is the integration half's."""
from types import SimpleNamespace

import pytest

from mailmind.bootstrap.mailbox import store
from mailmind.mailbox.application.commands import MailCommands
from mailmind.mailbox.domain import command
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.profile import ReadOnlyError

ADA = "ada@example.test"


class _Server:
    def __init__(self):
        self.tries = []

    def apply(self, row, first_try=False, timeout=None):
        self.tries.append(row["id"])
        return command.ServerAnswer(dest="Folders/Clients")


def a_message():
    with UnitOfWork().write() as c:
        return store.upsert_message(c, {"account": ADA, "kind": "imap", "folder": "INBOX", "uid": 5,
                                        "message_id": "<client-1@example.test>", "gmail_thread": None,
                                        "from_addr": "harbor@example.test", "from_name": "Harbor", "subject": "The berth",
                                        "date_iso": None, "ts": 1, "snippet": "", "bucket": "personal/protected", "is_junk": 0})


def _commands(server):
    accounts = SimpleNamespace(get=lambda email: {"email": email}, trash_folder=lambda acct: "Trash",
                               profile=lambda acct: SimpleNamespace(archive_is_label_removal=False))
    return MailCommands(UnitOfWork(), store, accounts, server)


def test_a_read_only_process_refuses_a_change_and_queues_nothing(data_dir, monkeypatch):
    server = _Server()
    commands = _commands(server)
    pk = a_message()
    monkeypatch.setenv("MAILMIND_PROFILE", "read-only")
    with pytest.raises(ReadOnlyError):
        commands.accept_move("move", pk, "m-1", to="Folders/Clients")
    with pytest.raises(ReadOnlyError):
        commands.accept_flags(pk, {"seen": True}, "m-2")
    with UnitOfWork().read() as c:
        assert c.execute("SELECT COUNT(*) FROM mail_commands").fetchone()[0] == 0
        assert c.execute("SELECT folder, seen FROM messages WHERE pk=?", (pk,)).fetchone()[:] == ("INBOX", 0)
    assert server.tries == []


def test_a_read_only_process_applies_nothing_a_full_run_queued(data_dir, monkeypatch):
    server = _Server()
    commands = _commands(server)
    accepted = commands.accept_move("move", a_message(), "m-1", to="Folders/Clients")
    assert commands.leave_to_worker(accepted) == command.WAITING        # queued by a full run, for its worker
    monkeypatch.setenv("MAILMIND_PROFILE", "read-only")
    with UnitOfWork().read() as c:
        mid = c.execute("SELECT id FROM mail_commands").fetchone()[0]
    with pytest.raises(ReadOnlyError):                                  # nor does the tray's Retry ask again
        commands.retry(mid)
    claimed = []
    monkeypatch.setattr(commands.QUEUE, "drain", lambda *a: claimed.append(a))
    monkeypatch.setattr(commands.QUEUE, "run_forever", lambda drain: drain())    # one round of the worker's, as it starts
    commands.worker_loop()
    assert claimed == [] and server.tries == []
    with UnitOfWork().read() as c:
        assert c.execute("SELECT state, applied FROM mail_commands").fetchone()[:] == ("queued", 0)
