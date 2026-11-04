"""Which folder plays which part (mailmind.mailbox.domain.roles), and how an imaplib LIST answer
reads (mailmind.mailbox.adapters.imap_list): SPECIAL-USE first, then whole names, never a part of
one; a shallower path before a deeper one; and the rail and the moves through the app."""
import imaplib

import pytest

from fixtures import world as w
from mailmind.mailbox.adapters import imap_list
from mailmind.mailbox.domain import roles
from mailmind.mailbox.domain.roles import Folder


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


def test_a_shallower_folder_wins_over_a_deeper_one_of_the_same_name():
    fs = folders(("Folders/Archive",), ("Archive",), ("Folders", "\\Noselect"))
    assert roles.find(fs, "archive") == "Archive"


def test_among_equals_the_servers_order_decides():
    assert roles.find(folders(("Spam",), ("Junk",)), "junk") == "Spam"


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
    import accounts
    data = [(b'(\\HasNoChildren) "/" {11}', 'Café "x"\\'.encode()), b")",
            (b'(\\HasNoChildren) "/" {6}', b"a\r\nb\r\n"), b")"]
    (f,) = imap_list.parse(data)
    assert f.name == 'Café \\"x\\"\\\\'
    assert accounts.mailbox_arg(f.name) == '"Café \\"x\\"\\\\"'


class _Refusing:
    def list(self):
        return "NO", [b"[UNAVAILABLE] try later"]


def test_a_refused_list_reads_as_no_folders():
    assert imap_list.list_folders(_Refusing()) == []


# ── through the fake server and the app ────────────────────────────────────────────────────────

def test_on_the_fake_each_role_finds_its_special_use_folder(world):
    import accounts
    import mailops
    found = {}
    for acct in (w.ADA, w.BEA, w.CY):
        M = accounts.connect(accounts.get(acct), timeout=10)
        try:
            found[acct] = {r: mailops.find_role(M, r) for r in ("sent", "drafts", "archive", "all", "junk", "trash")}
        finally:
            M.logout()
    assert found == {
        w.ADA: {"sent": "Sent", "drafts": "Drafts", "archive": "Archive", "all": None, "junk": "Junk", "trash": "Trash"},
        w.BEA: {"sent": "Sent", "drafts": "Drafts", "archive": "Archive", "all": "All Mail", "junk": "Spam",
                "trash": "Trash"},
        w.CY: {"sent": "[Gmail]/Sent Mail", "drafts": "[Gmail]/Drafts", "archive": None, "all": "[Gmail]/All Mail",
               "junk": "[Gmail]/Spam", "trash": "[Gmail]/Trash"}}


def test_the_rail_gives_each_role_to_one_folder(app, world):
    from fakes.imap_server import Mailbox
    a = world.server.accounts[w.BEA]
    with world.server.lock:                          # a user folder named like a system one
        a.mailboxes["Folders/Archive"] = Mailbox("Folders/Archive", uidvalidity=1_700_000_998)
    rail = {f["name"]: f["role"] for f in app.get(f"/api/folders?account={w.BEA}").json["folders"]}
    assert (rail["Archive"], rail["All Mail"], rail["Folders/Archive"], rail["Spam"]) == \
        ("archive", "all", "custom", "junk")


def test_archive_on_gmail_without_an_archive_folder_still_files_by_label(app, world):
    name = "cy-inbox-02-thread-end.eml"
    assert app.post("/api/archive", {"pk": world.pk(name)}).json["ok"]
    assert (w.CY, "INBOX") not in world.where(name)["server"]


def test_imaplib_is_what_the_parser_reads(world):
    """The parser reads what imaplib hands back from a real LIST, not a guess at it."""
    import accounts
    M = accounts.connect(accounts.get(w.BEA), timeout=10)
    try:
        typ, data = M.list()
    finally:
        M.logout()
    assert typ == "OK" and isinstance(M, (imaplib.IMAP4, accounts.ReadOnlyIMAP))
    assert {f.name for f in imap_list.parse(data)} >= {"INBOX", "Archive", "All Mail", "Spam", "Folders/Clients"}
