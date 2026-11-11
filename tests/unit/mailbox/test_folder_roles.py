"""Which folder plays which part (mailmind.mailbox.domain.roles), and how an imaplib LIST answer
reads (mailmind.mailbox.adapters.imap.imap_list): SPECIAL-USE first, then whole names, never a part of
one; a shallower path before a deeper one; and the rail and the moves through the app."""
import imaplib

import pytest

from fixtures import world as w
from mailmind.mailbox.adapters.imap import imap_list
from mailmind.mailbox.domain import roles
from mailmind.mailbox.domain.roles import Folder
from mailmind.bootstrap import mailbox


def folders(*specs):
    """Folders from (name, attributes...) pairs, with "/" as the delimiter."""
    return [Folder(name, frozenset(a.lower() for a in attrs)) for name, *attrs in specs]


@pytest.mark.parametrize("name, role", [
    ("INBOX", "inbox"), ("inbox", "inbox"),
    ("Sent", "sent"), ("Sent Items", "sent"), ("Sent Messages", "sent"), ("[Gmail]/Sent Mail", "sent"),
    ("INBOX.Sent", "sent"), ("Drafts", "drafts"), ("Draft", "drafts"),
    ("Archive", "archive"), ("Archives", "archive"), ("[Gmail]/All Mail", "all"),
    ("Junk", "junk"), ("Spam", "junk"), ("Junk E-mail", "junk"), ("Bulk Mail", "junk"), ("INBOX.Spam", "junk"),
    ("Trash", "trash"), ("Deleted Items", "trash"), ("Deleted Messages", "trash"), ("Bin", "trash"),
    ("Bulk orders", "custom"), ("Spamalot", "custom"), ("Sent from phone", "custom"), ("Sent Drafts", "custom"),
    ("Archive/2024", "custom"), ("Clients", "custom"), ("", "custom")])
def test_a_name_plays_a_role_only_whole(name, role):
    assert roles.role_by_name(name) == role


def test_the_attribute_wins_over_the_name():
    fs = folders(("Bulk orders", "\\Junk"), ("Junk",), ("Spam", "\\HasNoChildren"))
    assert [roles.role_of(f) for f in fs] == ["junk", "junk", "junk"]
    assert roles.find(fs, "junk") == "Bulk orders"          # the server said so


@pytest.mark.parametrize("name, says", [
    ("Drafts", True), ("Draft", True), ("drafts", True), ("INBOX.Drafts", True), ("INBOX/Drafts", True),
    ("[Gmail]/Drafts", True), ("[Google Mail]/Drafts", True),
    ("Contract drafts", False), ("Clients/Draft", False), ("Folders/Drafts", False), ("INBOX/Clients/Drafts", False),
    ("Sent", False), ("INBOX", False), ("", False), (None, False)])
def test_a_name_alone_says_drafts_only_where_a_server_keeps_them(name, says):
    assert roles.names_drafts(name) is says


def test_a_shallower_folder_wins_over_a_deeper_one_of_the_same_name():
    fs = folders(("Folders/Archive",), ("Archive",), ("Folders", "\\Noselect"))
    assert roles.find(fs, "archive") == "Archive"


@pytest.mark.parametrize("deeper", ["Old/Trash", "Old.Trash", "A/Trash"])
def test_with_no_delimiter_the_shallower_folder_still_wins(deeper):
    """A server whose LIST says NIL names no delimiter, so "/" and "." mark the depth, as they do the role."""
    fs = [Folder(deeper, frozenset(), None), Folder("Trash", frozenset(), None)]
    assert roles.find(fs, "trash") == "Trash"


def test_among_equals_the_name_decides_whatever_order_the_server_lists_them_in():
    for listed in ((("Spam",), ("Junk",)), (("Junk",), ("Spam",))):
        assert roles.find(folders(*listed), "junk") == "Junk"


def test_a_folder_that_cant_be_selected_plays_no_role():
    assert roles.find(folders(("Junk", "\\Noselect", "\\Junk"), ("Archive",)), "junk") is None


def test_inbox_is_the_inbox_whatever_it_is_flagged():
    fs = folders(("INBOX", "\\Archive"), ("Archive",))
    assert roles.role_of(fs[0]) == "inbox"
    assert roles.find(fs, "archive") == "Archive"


def test_find_takes_the_first_role_any_folder_plays():
    gmail = folders(("INBOX",), ("[Gmail]", "\\Noselect"), ("[Gmail]/All Mail", "\\All"))
    assert roles.find(gmail, "archive", "all") == "[Gmail]/All Mail"
    proton = folders(("Archive", "\\Archive"), ("All Mail", "\\All"))
    assert roles.find(proton, "archive", "all") == "Archive"
    assert roles.find(proton, "sent") is None


def test_the_servers_delimiter_splits_the_path():
    assert roles.role_of(Folder("INBOX.Junk", frozenset(), ".")) == "junk"
    assert roles.role_of(Folder("Mail/Junk.old", frozenset(), "/")) == "custom"
    assert roles.role_by_name("Mail/Junk.old") == "custom"      # a stored name: both separate
    assert roles.role_by_name("INBOX.Trash") == "trash"


# ── reading LIST ───────────────────────────────────────────────────────────────────────────────

def test_list_lines_read_as_folders():
    data = [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasChildren \\Noselect) "/" "[Gmail]"',
            b'(\\HasNoChildren \\All) "/" "[Gmail]/All Mail"', b'(\\HasNoChildren) "." INBOX.Spam',
            b'(\\HasNoChildren) NIL "Flat"', b'(\\HasNoChildren) "/" "Say \\"hi\\""',
            b'* garbage', b'', None]
    assert imap_list.parse(data) == [
        Folder("INBOX", frozenset({"\\hasnochildren"}), "/"),
        Folder("[Gmail]", frozenset({"\\haschildren", "\\noselect"}), "/"),
        Folder("[Gmail]/All Mail", frozenset({"\\hasnochildren", "\\all"}), "/"),
        Folder("INBOX.Spam", frozenset({"\\hasnochildren"}), "."),
        Folder("Flat", frozenset({"\\hasnochildren"}), None),
        Folder('Say \\"hi\\"', frozenset({"\\hasnochildren"}), "/")]


def test_a_literal_name_is_escaped_to_the_quoted_form_and_a_line_break_left_out():
    from mailmind.bootstrap import accounts
    data = [(b'(\\HasNoChildren) "/" {11}', 'Café "x"\\'.encode()), b")",
            (b'(\\HasNoChildren) "/" {6}', b"a\r\nb\r\n"), b")"]
    (f,) = imap_list.parse(data)
    assert f.name == 'Café \\"x\\"\\\\'
    assert accounts.mailbox_arg(f.name) == '"Café \\"x\\"\\\\"'


class _Refusing:
    def list(self):
        return "NO", [b"[UNAVAILABLE] try later"]


def test_a_refused_list_raises_and_find_role_falls_back():
    from mailmind.mailbox.adapters.imap.imaplib_session import ImaplibSession
    from mailmind.mailbox.application.session import find_role
    with pytest.raises(imaplib.IMAP4.error, match="LIST refused"):
        imap_list.list_folders(_Refusing())
    assert find_role(ImaplibSession(_Refusing()), "sent") is None


def test_stored_folders_play_their_roles_by_name(data_dir):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        c.executemany("INSERT INTO sync_state(account, folder) VALUES (?, ?)",
                      [(w.ADA, name) for name in ("Folders/Archive", "Archive", "INBOX.Spam", "Bulk orders", "Sent")])
        c.commit()
    finally:
        c.close()
    rail = {f["name"]: f["role"] for f in mailbox.folders._stored_folder_list(w.ADA)}
    assert rail == {"INBOX": "inbox", "Sent": "sent", "Archive": "archive", "INBOX.Spam": "junk",
                    "Folders/Archive": "custom", "Bulk orders": "custom"}
    assert [f["name"] for f in mailbox.folders._stored_folder_list(w.ADA)][:3] == ["INBOX", "Sent", "Archive"]
