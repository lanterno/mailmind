"""Where a filing verb sends a message (mailmind.mailbox.domain.destination): Gmail's archive drops the \\Inbox label, and
everywhere else archiving goes to the folder playing the archive role. Nothing here reaches a server."""
import pytest

from mailmind.mailbox.domain.destination import Destination, resolve


def asked(*roles):
    raise AssertionError(f"no reason to ask for {roles}")


def where(kind, *, named=None, by_label=False, trash="Trash", junk_fallback=None, find=asked):
    return resolve(kind, named=named, archive_is_label_removal=by_label, trash=trash, junk_fallback=junk_fallback, find=find)


def playing(**folders):
    """The server's answer: the folder playing the first of the roles asked that one plays."""
    return lambda *roles: next((folders[r] for r in roles if r in folders), None)


def test_gmails_archive_goes_nowhere_and_asks_nothing():
    assert where("archive", by_label=True) == Destination(drop_inbox_label=True)


def test_gmails_archive_drops_the_label_even_with_a_folder_named():
    assert where("archive", named="Receipts", by_label=True) == Destination(drop_inbox_label=True)


def test_elsewhere_an_archive_goes_to_the_archive_role_and_all_mail_only_without_one():
    assert where("archive", find=playing(archive="Archive", all="All Mail")) == Destination("Archive")
    assert where("archive", find=playing(all="All Mail")) == Destination("All Mail")


def test_a_server_with_nowhere_to_archive_says_so():
    assert where("archive", find=playing()) == Destination()


@pytest.mark.parametrize("kind", ("trash", "archive", "junk", "move"))
def test_a_folder_the_command_names_is_where_it_goes(kind):
    assert where(kind, named="Receipts") == Destination("Receipts")


def test_a_trash_goes_to_the_accounts_trash_and_asks_nothing():
    assert where("trash", trash="[Gmail]/Trash") == Destination("[Gmail]/Trash")


def test_junk_goes_to_the_junk_role_and_the_providers_name_only_without_one():
    assert where("junk", junk_fallback="[Gmail]/Spam", find=playing(junk="Junk")) == Destination("Junk")
    assert where("junk", junk_fallback="[Gmail]/Spam", find=playing()) == Destination("[Gmail]/Spam")
    assert where("junk", find=playing()) == Destination()


def test_a_move_naming_nothing_goes_nowhere():
    assert where("move") == Destination()
