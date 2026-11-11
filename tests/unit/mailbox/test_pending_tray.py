"""The pending-changes tray over the command path (mailmind.mailbox.application.commands.MailCommands.list_open and discard),
over the store and its queue in a temp database, with a server that can't be reached: what a queued flag change shows, and
what its Discard gives back. The tray's routes, Retry and the worker against the fake server are the integration half's."""
from types import SimpleNamespace

from mailmind.bootstrap.mailbox import store
from mailmind.mailbox.application.commands import MailCommands
from mailmind.mailbox.domain import command
from mailmind.platform.db.uow import UnitOfWork

ADA = "ada@example.test"


class _Away:
    """A server that can't be reached: every try leaves the change for the worker."""
    def apply(self, row, first_try=False, timeout=None):
        raise OSError("the connection went")


def a_message():
    """Ada's receipt in her Inbox, unstarred."""
    with UnitOfWork().write() as c:
        return store.upsert_message(c, {"account": ADA, "kind": "imap", "folder": "INBOX", "uid": 5,
                                        "message_id": "<receipt-1042@example.test>", "gmail_thread": None,
                                        "from_addr": "shop@example.test", "from_name": "Shop", "subject": "Receipt 1042",
                                        "date_iso": None, "ts": 1, "snippet": "", "bucket": "receipt", "is_junk": 0})


def flagged(pk):
    with UnitOfWork().read() as c:
        return store.get_message(c, pk)["flagged"]


def star_queued():
    """A star asked for while the server is away: (the command path, the message, the command)."""
    accounts = SimpleNamespace(get=lambda email: {"email": email})
    commands = MailCommands(UnitOfWork(), store, accounts, _Away())
    pk = a_message()
    accepted = commands.accept_flags(pk, {"flagged": True}, "f-1")
    assert commands.submit(accepted) == command.QUEUED
    return commands, pk, accepted.id


def test_a_queued_flag_change_names_its_message_and_what_it_asks(data_dir):
    commands, _, mid = star_queued()
    got = commands.list_open()
    [item] = got["items"]
    assert (item["id"], item["op"], item["flags"], item["subject"]) == (mid, "flag", {"flagged": True}, "Receipt 1042")
    assert (got["waiting"], got["stuck"]) == (1, 0)


def test_discard_of_a_flag_change_puts_the_flags_back(data_dir):
    commands, pk, mid = star_queued()
    assert flagged(pk) == 1
    assert commands.discard(mid)["id"] == mid
    assert flagged(pk) == 0 and commands.list_open()["items"] == []
