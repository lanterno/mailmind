"""Which folder plays a role on a session's server (mailmind.mailbox.application.session.find_role): the first of the roles
asked that one plays, none when the server refuses its LIST so the caller falls back to its default name, and a lost
connection raised."""
import imaplib

import pytest

from mailmind.mailbox.application.session import find_role
from mailmind.mailbox.domain.roles import Folder


class _Lists:
    def __init__(self, answer):
        self.answer = answer

    def folders(self):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


FOLDERS = [Folder("INBOX"), Folder("Archives", frozenset({"\\archive"})), Folder("Tout", frozenset({"\\all"}))]


@pytest.mark.parametrize("wanted, found", [(("archive", "all"), "Archives"), (("all", "archive"), "Tout"),
                                           (("junk", "all"), "Tout"), (("junk",), None)])
def test_the_first_role_asked_that_a_folder_plays(wanted, found):
    assert find_role(_Lists(FOLDERS), *wanted) == found


def test_a_list_the_server_refuses_finds_none():
    assert find_role(_Lists(imaplib.IMAP4.error("LIST refused")), "archive") is None


def test_a_connection_lost_while_listing_raises():
    with pytest.raises(imaplib.IMAP4.abort):
        find_role(_Lists(imaplib.IMAP4.abort("the connection went")), "archive")
