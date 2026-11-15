"""A Gmail App Password typed as Google shows it, four groups of four letters with spaces between (account.as_kept): kept,
and tried, without its spaces, at an account that strips them, a Gmail account's by default. Any other password, and any
password at an account that doesn't strip spaces, is kept as it was typed. The Keychain is the guard's stand-in."""
import pytest

from mailmind.accounts.adapters import config_file, secrets
from mailmind.accounts.application.directory import Directory
from mailmind.accounts.domain.account import as_kept
from mailmind.accounts.domain.credential import Typed
from mailmind.accounts.domain.draft import throwaway_account

DEE = "dee@example.test"
DEES = "account:dee@example.test"
SHOWN = "abcd efgh ijkl mnop"


@pytest.mark.parametrize("typed, kept", [
    (SHOWN, "abcdefghijklmnop"),
    ("ABCD EFGH IJKL MNOP", "ABCDEFGHIJKLMNOP"),
    ("abcdefghijklmnop", "abcdefghijklmnop"),
    ("abcd\u00a0efgh\u00a0ijkl\u00a0mnop", "abcdefghijklmnop"),       # a no-break space, as a copy from the page may bring
    ("ab cd ef gh ij kl mn op", "abcdefghijklmnop"),
    ("abcd efgh ijkl mno", "abcd efgh ijkl mno"),                     # fifteen letters: not an App Password
    ("abcd efgh ijkl mnopq", "abcd efgh ijkl mnopq"),                 # seventeen
    ("abcd efgh ijkl mn0p", "abcd efgh ijkl mn0p"),                   # a digit
    ("abcd-efgh-ijkl-mnop", "abcd-efgh-ijkl-mnop"),
    ("abcd efgh ijkl mnöp", "abcd efgh ijkl mnöp"),                   # a letter outside ASCII
    ("abcd\tefgh ijkl mnop", "abcd\tefgh ijkl mnop"),                 # a tab is no space Google shows
    ("correct horse battery staple", "correct horse battery staple"),
])
def test_an_app_password_as_google_shows_it_is_kept_without_its_spaces(typed, kept):
    assert as_kept(typed, True) == kept


@pytest.mark.parametrize("strip", [False, None, 0])
def test_an_account_that_keeps_spaces_keeps_them(strip):
    assert as_kept(SHOWN, strip) == SHOWN


@pytest.fixture
def directory(data_dir):
    return Directory(config_file.ConfigFile(), secrets, lambda: None)


def typed(password):
    return {"style": "keychain", "value": password}


def test_a_gmail_account_added_with_its_app_password_as_shown_keeps_it_whole(directory, fake_keychain):
    directory.add_account({"email": DEE, "type": "gmail", "credential": typed(SHOWN)})
    assert fake_keychain.items() == {DEES: "abcdefghijklmnop"}
    assert directory.resolve_credential(directory.accounts()[0]) == "abcdefghijklmnop"


def test_an_imap_account_keeps_its_password_as_typed(directory, fake_keychain):
    directory.add_account({"email": DEE, "type": "imap", "host": "imap.example.test", "credential": typed(SHOWN)})
    assert fake_keychain.items() == {DEES: SHOWN}


def test_an_account_says_whether_it_strips_spaces(directory, fake_keychain):
    directory.add_account({"email": DEE, "type": "gmail", "strip_spaces": False, "credential": typed(SHOWN)})
    assert fake_keychain.items() == {DEES: SHOWN}
    directory.update_account(DEE, {"strip_spaces": True, "credential": typed(SHOWN)})
    assert fake_keychain.items() == {DEES: "abcdefghijklmnop"}


def test_an_edit_that_makes_an_account_gmail_keeps_its_new_password_whole(directory, fake_keychain):
    directory.add_account({"email": DEE, "type": "imap", "host": "imap.example.test", "credential": typed("before")})
    directory.update_account(DEE, {"type": "gmail", "credential": typed(SHOWN)})
    assert fake_keychain.items() == {DEES: "abcdefghijklmnop"}


def test_a_password_kept_from_the_command_line_is_kept_alike(directory, fake_keychain):
    directory.add_account({"email": DEE, "type": "gmail", "credential": typed("before")})
    directory.set_password(DEE, SHOWN)
    assert fake_keychain.items() == {DEES: "abcdefghijklmnop"}


@pytest.mark.parametrize("draft, tried", [
    ({"type": "gmail"}, "abcdefghijklmnop"),
    ({"type": "gmail", "strip_spaces": False}, SHOWN),
    ({"type": "imap", "host": "imap.example.test"}, SHOWN),
    ({"type": "imap", "host": "imap.example.test", "strip_spaces": True}, "abcdefghijklmnop"),
], ids=str)
def test_a_draft_tries_its_password_as_it_would_be_kept(draft, tried):
    assert throwaway_account({"email": DEE, "credential": typed(SHOWN), **draft})["credential"] == Typed(tried)
