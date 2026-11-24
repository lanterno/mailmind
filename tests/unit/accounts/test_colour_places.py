"""Which of the palette's colours each account wears (account.colour_places): a place kept in the account's entry, so both
clients colour an account the same and removing one moves no other's. A file that keeps none colours them by their order."""
import json

import pytest

from mailmind.accounts.adapters import config_file, secrets
from mailmind.accounts.application.directory import Directory
from mailmind.accounts.domain import account

ADA, BEA, CY, DEE = "ada@example.test", "bea@example.test", "cy@example.test", "dee@example.test"


@pytest.fixture
def directory(data_dir):
    (data_dir / "mailmind.config.json").write_text(json.dumps({"accounts": [
        {"email": e, "type": "imap", "host": "imap.example.test", "credential": {"env": "MM_TEST_PASSWORD"}}
        for e in (ADA, BEA, CY)]}))
    return Directory(config_file.ConfigFile(), secrets, lambda: None)


def kept(data_dir):
    return {a["email"]: a.get("colour") for a in json.loads((data_dir / "mailmind.config.json").read_text())["accounts"]}


def test_a_file_that_keeps_no_colour_colours_the_accounts_by_their_order(directory, data_dir):
    assert directory.colour_map() == {ADA: 0, BEA: 1, CY: 2}
    assert kept(data_dir) == {ADA: None, BEA: None, CY: None}          # reading writes nothing


def test_an_account_removed_moves_no_other_accounts_colour_and_one_added_takes_the_place_it_left(directory, data_dir,
                                                                                                fake_keychain):
    assert directory.remove_account(BEA)
    assert directory.colour_map() == {ADA: 0, CY: 2} and kept(data_dir) == {ADA: 0, CY: 2}
    directory.add_account({"email": DEE, "type": "imap", "host": "imap.example.test",
                           "credential": {"style": "keychain", "value": "dee-password"}})
    assert directory.colour_map() == {ADA: 0, CY: 2, DEE: 1} and kept(data_dir)[DEE] == 1


def test_an_accounts_own_colour_stands_unless_another_before_it_holds_it_or_it_is_no_place():
    entries = [{"email": ADA, "colour": 3}, {"email": BEA, "colour": 3}, {"email": CY, "colour": -1},
               {"email": DEE, "colour": "1"}, {"email": "eve@example.test", "colour": True}, {"colour": 0}]
    assert account.colour_places(entries) == {ADA: 3, BEA: 0, CY: 1, DEE: 2, "eve@example.test": 4}
