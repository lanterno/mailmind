"""The command queue's worker (mailmind.mailbox.application.commands.MailCommands.worker_loop) in a read-only process, over the
store and its queue in a temp database: it drains through MailCommands.drain, which applies nothing until a full run. What a
crash left running, put back and applied through the fake server, is the integration half's."""
from types import SimpleNamespace

from mailmind.bootstrap.mailbox import store
from mailmind.mailbox.application.commands import MailCommands
from mailmind.mailbox.domain import command
from mailmind.platform.db.uow import UnitOfWork

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


def test_a_read_only_process_applies_nothing(data_dir, monkeypatch):
    accounts = SimpleNamespace(get=lambda email: {"email": email}, trash_folder=lambda acct: "Trash",
                               profile=lambda acct: SimpleNamespace(archive_is_label_removal=False))
    server = _Server()
    commands = MailCommands(UnitOfWork(), store, accounts, server)
    monkeypatch.setenv("MAILMIND_PROFILE", "read-only")
    accepted = commands.accept_move("move", a_message(), "m-1", to="Folders/Clients")
    assert commands.submit(accepted) == command.QUEUED                 # no try in a click either: the worker's to make
    claimed = []
    monkeypatch.setattr(commands.QUEUE, "drain", lambda *a: claimed.append(a))
    monkeypatch.setattr(commands.QUEUE, "run_forever", lambda drain: drain())    # one round of the worker's, as it starts
    commands.worker_loop()
    assert claimed == [] and server.tries == []
    with UnitOfWork().read() as c:
        assert c.execute("SELECT state, applied FROM mail_commands").fetchone()[:] == ("queued", 0)
