"""Which folder is an account's Trash on its server (mailmind.mailbox.domain.roles.trash, and session.find_trash on a session):
the one the account names when the server lists it, else the folder LIST marks \\Trash, then one whose name says trash, else
the one the account names. The Rust core's crates/mm-mailbox/tests/folders.rs holds the same."""
import imaplib

import pytest

from mailmind.mailbox.application.session import find_trash
from mailmind.mailbox.domain.roles import Folder, trash

GMAIL_DE = [Folder("INBOX"), Folder("[Google Mail]/Papierkorb", frozenset({"\\trash"})),
            Folder("Trash", frozenset({"\\noselect"}))]


@pytest.mark.parametrize("folders, named, found", [
    (GMAIL_DE, "[Gmail]/Trash", "[Google Mail]/Papierkorb"),
    (GMAIL_DE, "Trash", "[Google Mail]/Papierkorb"),
    ([Folder("INBOX"), Folder("Deleted Messages")], "Trash", "Deleted Messages"),
    ([Folder("Trash"), Folder("Deleted Messages", frozenset({"\\trash"}))], "Trash", "Trash"),
    ([Folder("INBOX")], "Trash", "Trash"),
], ids=["localised-gmail", "localised-gmail-as-imap", "icloud", "its-own-listed", "none-plays-it"])
def test_trash_is_the_accounts_own_where_the_server_lists_it_and_else_the_folder_that_plays_trash(folders, named, found):
    assert trash(folders, named) == found


class _Lists:
    def __init__(self, answer):
        self.answer = answer

    def folders(self):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_a_session_finds_it_among_the_folders_its_server_lists():
    assert find_trash(_Lists(GMAIL_DE), "[Gmail]/Trash") == "[Google Mail]/Papierkorb"


def test_a_list_the_server_refuses_keeps_the_accounts_own():
    assert find_trash(_Lists(imaplib.IMAP4.error("LIST refused")), "Trash") == "Trash"


def test_a_connection_lost_while_listing_raises():
    with pytest.raises(imaplib.IMAP4.abort):
        find_trash(_Lists(imaplib.IMAP4.abort("the connection went")), "Trash")
