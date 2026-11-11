"""The copies Compose keeps on the server (mailmind.mailbox.application.copies.Copies), on a fake session: a sent message
in Sent, a draft in Drafts and what goes when it's saved again, each on a session of its own that is always closed, and
never a bulk EXPUNGE."""
import imaplib

import pytest

from mailmind.mailbox.application.copies import Copies
from mailmind.mailbox.domain.roles import Folder

ACCT = {"email": "ada@example.test"}
FOLDERS = [Folder("INBOX"), Folder("Sent Messages", frozenset({"\\sent"})), Folder("Brouillons", frozenset({"\\drafts"}))]


class Selected:
    def __init__(self, uidvalidity):
        self.uidvalidity = uidvalidity


class _Session:
    """One folder's worth of a server. `found` answers a header search by Message-ID; `refuse` names calls that get a NO."""

    def __init__(self, folders=FOLDERS, uidvalidity=5, found=None, refuse=(), lose=(), logs_out=True, next_uid=100):
        self._folders, self.uidvalidity, self.found = folders, uidvalidity, dict(found or {})
        self.refuse, self.lose, self.logs_out, self.next_uid = set(refuse), set(lose), logs_out, next_uid
        self.said = []

    def _call(self, name, *args):
        self.said.append((name, *args))
        if name in self.lose:
            raise imaplib.IMAP4.abort("the connection went")
        if name in self.refuse:
            raise imaplib.IMAP4.error(f"{name} refused")

    def folders(self):
        return self._folders

    def select(self, folder, readonly=True):
        self._call("select", folder, readonly)
        return Selected(self.uidvalidity)

    def uids(self, *criteria):
        self._call("search", *criteria)
        if criteria[0] == "UID":
            return [int(criteria[1])] if int(criteria[1]) in self.found.values() else []
        mid = criteria[-1].strip('"')
        return [self.found[mid]] if mid in self.found else []

    def append(self, folder, raw, *, flags=(), when=None):
        self._call("append", folder, tuple(flags))
        assert when is not None and when.tzinfo is not None
        return self.next_uid

    def add_flags(self, uids, flags):
        self._call("flag", list(uids), list(flags))

    def expunge(self, uids):
        self._call("expunge", list(uids))

    def logout(self):
        self.said.append(("logout",))
        if not self.logs_out:
            raise OSError("the connection went")


def copies(session):
    def open_session(acct, timeout=None):
        if isinstance(session, Exception):
            raise session
        return session
    return Copies(open_session, lambda value: f'"{value}"')


def kinds(session):
    return [s[0] for s in session.said]


# -- Sent --------------------------------------------------------------------------------------------------------

def test_a_sent_message_goes_in_the_folder_playing_sent_marked_seen():
    s = _Session()
    assert copies(s).append_to_sent(ACCT, b"raw") == "Sent Messages"
    assert s.said == [("append", "Sent Messages", ("\\Seen",)), ("logout",)]


def test_with_no_folder_playing_sent_it_goes_in_sent():
    s = _Session(folders=[Folder("INBOX")])
    assert copies(s).append_to_sent(ACCT, b"raw") == "Sent"


def test_a_copy_an_earlier_try_left_is_kept_and_none_is_added():
    s = _Session(found={"<m1@example.test>": 9})
    assert copies(s).append_to_sent(ACCT, b"raw", message_id="<m1@example.test>") == "Sent Messages"
    assert kinds(s) == ["select", "search", "logout"]
    assert s.said[1] == ("search", "UNDELETED", "HEADER", "Message-ID", '"<m1@example.test>"')


def test_with_no_copy_left_one_is_added():
    s = _Session()
    copies(s).append_to_sent(ACCT, b"raw", message_id="<m1@example.test>")
    assert kinds(s) == ["select", "search", "append", "logout"]


def test_a_refused_append_raises_and_the_session_is_still_closed():
    s = _Session(refuse={"append"}, logs_out=False)
    with pytest.raises(imaplib.IMAP4.error):
        copies(s).append_to_sent(ACCT, b"raw")
    assert kinds(s)[-1] == "logout"


@pytest.mark.parametrize("found, there", [({"<m1@example.test>": 9}, True), ({}, False)])
def test_whether_a_message_is_in_sent(found, there):
    s = _Session(found=found)
    assert copies(s).in_sent(ACCT, "<m1@example.test>") is there
    assert s.said[0] == ("select", "Sent Messages", True) and kinds(s)[-1] == "logout"


# -- Drafts ------------------------------------------------------------------------------------------------------

def test_a_draft_goes_in_drafts_and_the_copy_before_it_goes_by_its_uid_alone():
    s = _Session()
    assert copies(s).append_to_drafts(ACCT, b"raw", prev_uid=42, prev_uidvalidity=5) == ("Brouillons", 100, 5, 42)
    assert s.said == [("select", "Brouillons", False), ("append", "Brouillons", ("\\Draft",)),
                      ("flag", [42], ["\\Deleted"]), ("expunge", [42]), ("logout",)]


def test_a_draft_saved_before_a_renumber_is_found_by_its_message_id():
    s = _Session(found={"<d1@example.test>": 43})
    out = copies(s).append_to_drafts(ACCT, b"raw", prev_uid=42, prev_uidvalidity=4, prev_msgid="<d1@example.test>")
    assert out == ("Brouillons", 100, 5, 43)


def test_a_draft_that_cant_be_found_again_is_left_beside_the_new_one():
    s = _Session()
    assert copies(s).append_to_drafts(ACCT, b"raw", prev_uid=42, prev_uidvalidity=4) == ("Brouillons", 100, 5, None)
    assert "expunge" not in kinds(s)


def test_a_search_for_the_old_copy_that_fails_leaves_it():
    s = _Session(refuse={"search"})
    assert copies(s).append_to_drafts(ACCT, b"raw", prev_msgid="<d1@example.test>")[3] is None
    assert "append" in kinds(s) and "expunge" not in kinds(s)


def test_a_draft_whose_copy_is_in_another_folder_deletes_nothing_here():
    s = _Session(found={"<d1@example.test>": 43})
    out = copies(s).append_to_drafts(ACCT, b"raw", prev_uid=42, prev_uidvalidity=5, prev_msgid="<d1@example.test>",
                                     prev_folder="Drafts (old)")
    assert out == ("Brouillons", 100, 5, None) and "expunge" not in kinds(s)


def test_a_drafts_folder_that_wont_open_still_takes_the_draft_and_deletes_nothing():
    s = _Session(refuse={"select"})
    assert copies(s).append_to_drafts(ACCT, b"raw", prev_uid=42, prev_uidvalidity=5) == ("Brouillons", 100, None, None)
    assert "expunge" not in kinds(s)


def test_a_drafts_folder_lost_on_the_way_raises():
    s = _Session(lose={"select"})
    with pytest.raises(imaplib.IMAP4.abort):
        copies(s).append_to_drafts(ACCT, b"raw")
    assert kinds(s)[-1] == "logout"


def test_this_very_draft_left_by_an_earlier_try_stands_and_none_is_added():
    s = _Session(found={"<d2@example.test>": 77})
    out = copies(s).append_to_drafts(ACCT, b"raw", prev_uid=42, prev_uidvalidity=5, message_id="<d2@example.test>",
                                     tried="<d2@example.test>")
    assert out == ("Brouillons", 77, 5, 42) and "append" not in kinds(s)


def test_another_draft_left_by_an_earlier_try_goes_once_the_new_one_is_there():
    s = _Session(found={"<d1@example.test>": 76})
    out = copies(s).append_to_drafts(ACCT, b"raw", message_id="<d2@example.test>", tried="<d1@example.test>")
    assert out == ("Brouillons", 100, 5, None)
    assert kinds(s) == ["select", "search", "append", "flag", "expunge", "logout"]
    assert s.said[4] == ("expunge", [76])


def test_an_old_copy_that_wont_go_is_left_and_the_save_still_stands():
    s = _Session(refuse={"expunge"})
    assert copies(s).append_to_drafts(ACCT, b"raw", prev_uid=42, prev_uidvalidity=5) == ("Brouillons", 100, 5, None)


def test_a_search_for_what_a_try_left_lost_on_the_way_raises():
    s = _Session(lose={"search"})
    with pytest.raises(imaplib.IMAP4.abort):
        copies(s).append_to_drafts(ACCT, b"raw", tried="<d1@example.test>")


def test_a_search_for_what_a_try_left_that_is_refused_finds_nothing():
    s = _Session(refuse={"search"})
    assert copies(s).append_to_drafts(ACCT, b"raw", tried="<d1@example.test>")[1] == 100


# -- removing a draft --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("found, removed", [({"<d1@example.test>": 76}, True), ({}, False)])
def test_the_copy_a_try_left_is_removed_if_it_is_there(found, removed):
    s = _Session(found=found)
    assert copies(s).delete_left_copy(ACCT, "<d1@example.test>") is removed
    assert ("expunge", [76]) in s.said if removed else "expunge" not in kinds(s)
    assert kinds(s)[-1] == "logout"


def test_a_draft_goes_by_its_uid_alone():
    s = _Session()
    assert copies(s).delete_draft_uid(ACCT, "Brouillons", 42, uidvalidity=5) is True
    assert s.said == [("select", "Brouillons", False), ("flag", [42], ["\\Deleted"]), ("expunge", [42]), ("logout",)]


def test_a_draft_with_no_folder_named_goes_from_drafts():
    s = _Session()
    assert copies(s).delete_draft_uid(ACCT, None, 42) is True
    assert s.said[0] == ("select", "Drafts", False)


@pytest.mark.parametrize("uid, session, flagged", [
    pytest.param(None, _Session(), False, id="no-uid"),
    pytest.param(42, _Session(refuse={"select"}), False, id="folder-refused"),
    pytest.param(42, _Session(uidvalidity=6), False, id="renumbered"),
    pytest.param(42, _Session(refuse={"expunge"}), True, id="expunge-refused"),
])
def test_a_draft_that_cant_safely_go_stays(uid, session, flagged):
    assert copies(session).delete_draft_uid(ACCT, "Brouillons", uid, uidvalidity=5) is False
    assert ("flag" in kinds(session)) is flagged


@pytest.mark.parametrize("lost", ["select", "expunge"])
def test_a_draft_removal_lost_on_the_way_raises(lost):
    s = _Session(lose={lost})
    with pytest.raises(imaplib.IMAP4.abort):
        copies(s).delete_draft_uid(ACCT, "Brouillons", 42)
    assert kinds(s)[-1] == "logout"


# -- a look ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("found, there", [({"<m@example.test>": 42}, True), ({}, False)])
def test_a_probe_says_the_folders_uidvalidity_and_whether_the_uid_is_there(found, there):
    s = _Session(found=found)
    assert copies(s).probe(ACCT, "INBOX", 42, timeout=5) == (5, there)
    assert s.said[0] == ("select", "INBOX", True)


class _TimesOut(_Session):
    def select(self, folder, readonly=True):
        raise TimeoutError("timed out")


@pytest.mark.parametrize("session", [OSError("no route to host"), _Session(refuse={"select"}), _Session(lose={"search"}),
                                     _TimesOut()], ids=["unreachable", "refused", "lost", "timed-out"])
def test_a_probe_that_cant_tell_says_none(session):
    assert copies(session).probe(ACCT, "INBOX", 42, timeout=5) is None
