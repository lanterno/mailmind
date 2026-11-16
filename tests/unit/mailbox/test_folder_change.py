"""A folder a person makes, renames or deletes (mailmind.mailbox.domain.folder_change): the name they typed as IMAP keeps it,
which folders are theirs to change, and what the stored names become once one is renamed."""
import pytest

from mailmind.kernel.errors import Conflict, NotFound, Refused
from mailmind.mailbox.domain import folder_change
from mailmind.mailbox.domain.roles import Folder

RAIL = [Folder("INBOX"), Folder("Sent", frozenset({"\\sent"})), Folder("Drafts"), Folder("Archive"), Folder("Junk"),
        Folder("Trash"), Folder("Bin"), Folder("Clients"), Folder("Clients/2026"), Folder("Old", frozenset({"\\noselect"}))]
GMAIL = [Folder("INBOX"), Folder("[Gmail]", frozenset({"\\noselect"})), Folder("[Gmail]/All Mail", frozenset({"\\all"})),
         Folder("[Gmail]/Trash", frozenset({"\\trash"})), Folder("[Gmail]/Starred", frozenset({"\\flagged"})),
         Folder("[Gmail]/Important", frozenset({"\\important"})), Folder("[Gmail]/Chats"), Folder("Receipts"),
         Folder("Work/Clients")]
BRIDGE = [Folder("INBOX"), Folder("Trash", frozenset({"\\trash"})), Folder("Folders", frozenset({"\\noselect"})),
          Folder("Labels", frozenset({"\\noselect"})), Folder("Folders/Clients")]
COURIER = [Folder("INBOX", delimiter="."), Folder("INBOX.Sent", delimiter="."), Folder("INBOX.Trash", delimiter="."),
           Folder("INBOX.Clients", delimiter=".")]


@pytest.mark.parametrize("typed, listed", [
    ("Receipts", "Receipts"), ("  Receipts \t", "Receipts"), ("Été & co", "&AMk-t&AOk- &- co"), ("日本語", "&ZeVnLIqe-"),
    ("Tom & Jerry", "Tom &- Jerry"), ('Say "hi"', 'Say \\"hi\\"'), ("a\\b", "a\\\\b"), ("🙂", "&2D3eQg-")])
def test_a_typed_name_goes_as_list_gives_it_back(typed, listed):
    assert folder_change.as_listed(typed, "/") == listed


@pytest.mark.parametrize("typed, said", [
    ("", folder_change.NO_NAME), ("   ", folder_change.NO_NAME), (None, folder_change.NO_NAME), (7, folder_change.NO_NAME),
    ("x" * 201, folder_change.TOO_LONG), ("two\nlines", folder_change.UNSENDABLE), ("a\tb", folder_change.UNSENDABLE),
    ("Work/Clients", folder_change.NESTED.format("/"))])
def test_a_name_that_cant_be_one_is_refused_in_words_that_dont_repeat_it(typed, said):
    with pytest.raises(Refused) as refused:
        folder_change.as_listed(typed, "/")
    assert refused.value.said == said
    assert folder_change.as_listed("x" * 200, "/") == "x" * 200


def test_a_name_may_hold_a_slash_where_the_server_separates_with_a_dot():
    assert folder_change.as_listed("Work/Clients", ".") == "Work/Clients"
    with pytest.raises(Refused):
        folder_change.as_listed("Work.Clients", ".")


@pytest.mark.parametrize("name", ["INBOX", "inbox", "Sent", "Drafts", "Archive", "Junk", "Trash", "Bin"])
def test_the_folders_that_play_a_part_and_the_accounts_trash_are_never_changed(name):
    with pytest.raises(Refused) as refused:
        folder_change.changeable(RAIL, name, trash="Bin")
    assert refused.value.said == folder_change.PLAYS_A_PART


@pytest.mark.parametrize("name", ["[Gmail]/Starred", "[Gmail]/Important", "[Gmail]/Chats"])
def test_a_folder_the_server_keeps_is_never_changed(name):
    with pytest.raises(Refused) as refused:
        folder_change.changeable(GMAIL, name, trash="[Gmail]/Trash")
    assert refused.value.said == folder_change.SERVERS_OWN


def test_a_folder_with_a_special_use_attribute_is_the_servers_wherever_it_sits():
    folders = [Folder("INBOX"), Folder("Sent", frozenset({"\\sent"})), Folder("Virtual/Flagged", frozenset({"\\flagged"})),
               Folder("Old/Sent", frozenset({"\\sent"}))]
    for name in ("Virtual/Flagged", "Old/Sent"):
        with pytest.raises(Refused) as refused:
            folder_change.changeable(folders, name, trash="Trash")
        assert refused.value.said == folder_change.SERVERS_OWN


def test_the_rail_offers_to_change_only_a_persons_own():
    assert folder_change.changeable_names(GMAIL, "[Gmail]/Trash") == {"Receipts", "Work/Clients"}
    assert folder_change.changeable_names(RAIL, "Bin") == {"Clients", "Clients/2026"}


def test_a_folder_not_listed_or_not_selectable_is_gone():
    for name in ("Nowhere", "Old"):
        with pytest.raises(NotFound):
            folder_change.changeable(RAIL, name, trash="Trash")
    assert folder_change.changeable(RAIL, "Clients", trash="Trash").name == "Clients"


def test_a_folder_with_folders_inside_is_known():
    assert folder_change.has_folders_inside(RAIL, Folder("Clients"))
    assert not folder_change.has_folders_inside(RAIL, Folder("Clients/2026"))
    assert not folder_change.has_folders_inside(RAIL, Folder("Clients", delimiter=None))


def test_a_rename_keeps_the_folder_where_it_is():
    assert folder_change.renamed_to(Folder("Clients/2026"), "2027", RAIL) == "Clients/2027"
    assert folder_change.renamed_to(Folder("Clients"), "Customers", RAIL) == "Customers"
    assert folder_change.renamed_to(Folder("Clients"), "Clients", RAIL) == "Clients"
    with pytest.raises(Conflict):
        folder_change.renamed_to(Folder("Clients"), "Archive", RAIL)
    assert folder_change.renamed_to(Folder("INBOX.Clients", delimiter="."), "Customers", COURIER) == "INBOX.Customers"


@pytest.mark.parametrize("name, typed", [("R&-D", "R&D"), ("&AMk-t&AOk-", "Été"), ('Say \\"hi\\"', 'Say "hi"'),
                                         ("Clients/R&-D", " R&D "), ("Tab\tbed", "Tab\tbed")])
def test_a_folder_renamed_to_the_name_it_has_stays_as_it_is(name, typed):
    folder = Folder(name)
    assert folder_change.renamed_to(folder, typed, [*RAIL, folder]) == name


def test_a_new_folder_is_at_the_top_by_a_name_not_taken():
    assert folder_change.new_name("Receipts", RAIL) == "Receipts"
    assert folder_change.new_name("Receipts", [Folder("INBOX"), Folder("Clients")]) == "Receipts"
    for taken in ("Archive", "inbox"):
        with pytest.raises(Conflict):
            folder_change.new_name(taken, RAIL)


def test_a_new_folder_goes_where_the_account_keeps_a_persons_own():
    assert folder_change.new_name("Receipts", BRIDGE) == "Folders/Receipts"
    assert folder_change.new_name("Receipts", COURIER) == "INBOX.Receipts"
    assert folder_change.new_name("Bills", GMAIL) == "Bills"
    with pytest.raises(Conflict):
        folder_change.new_name("Clients", BRIDGE)
    with pytest.raises(Refused):
        folder_change.new_name("Folders/Receipts", BRIDGE)


def test_a_renamed_folders_stored_names_follow_it_with_the_folders_inside():
    assert folder_change.moved("Clients", "Clients", "Customers", "/") == "Customers"
    assert folder_change.moved("Clients/2026", "Clients", "Customers", "/") == "Customers/2026"
    assert folder_change.moved("Clientsville", "Clients", "Customers", "/") is None
    assert folder_change.moved("Clients/2026", "Clients", "Customers", None) is None
