"""Undoing a Gmail archive puts \\Inbox back through the folder playing All Mail, whatever the account's language calls
it: the role says which, and "[Gmail]/All Mail" is only what's left when the server won't list its folders."""
import imaplib

from mailmind.bootstrap.mailbox import store
from mailmind.mailbox.application.reversal import Reversals
from mailmind.mailbox.application.server_ops import ServerOps
from mailmind.mailbox.domain.roles import Folder

ADA = "ada@example.test"
ALL_MAIL = "[Google Mail]/Alle Nachrichten"
ENTRY = {"kind": "gmail_archive", "account": ADA, "message_id": "<note-1@example.test>"}


class _Session:
    def __init__(self, lists=True):
        self.lists = lists
        self.selected = None
        self.labelled = []

    def folders(self):
        if not self.lists:
            raise imaplib.IMAP4.error("no")
        return [Folder("INBOX", frozenset(), "/"), Folder(ALL_MAIL, frozenset({"\\all"}), "/")]

    def select(self, folder, *, readonly=True):
        self.selected = folder

    def by_message_id(self, message_id):
        return 7

    def add_labels(self, uids, labels):
        self.labelled.append((self.selected, uids, labels))

    def logout(self):
        pass


class _Accounts:
    def get(self, email):
        return {"email": email}


class _Ingest:
    def ingest_message(self, acct, folder, message_id):
        return 1


def _undo(session):
    server = ServerOps(store, _Accounts(), lambda acct: session, lambda value: value)
    return Reversals(store, _Accounts(), server, None, _Ingest()).gmail_archive(ENTRY)


def test_the_label_goes_back_through_the_folder_playing_all_mail(data_dir):
    session = _Session()
    assert _undo(session) == (True, ADA, "INBOX")
    assert session.labelled == [(ALL_MAIL, [7], ["\\Inbox"])]


def test_a_server_that_wont_list_its_folders_is_asked_for_gmails_own_name(data_dir):
    session = _Session(lists=False)
    assert _undo(session) == (True, ADA, "INBOX")
    assert session.labelled == [("[Gmail]/All Mail", [7], ["\\Inbox"])]
