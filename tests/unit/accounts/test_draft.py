"""mailmind.accounts.domain.draft: an unsaved account may only try a password typed with it, only against the host typed
with it, and keeps it nowhere. No server is needed to say so. characterization/accounts/test_new_accounts.py's route test
holds that nothing reaches a server, too."""
import json

import pytest

from mailmind.accounts.adapters import config_file
from mailmind.accounts.domain.credential import Typed
from mailmind.accounts.domain.draft import DraftRefused, throwaway_account

TYPED = {"style": "keychain", "value": "typed-password"}
SAVE_FIRST = "save the account to test a stored secret; before then only a typed password is tried"


def draft(**fields):
    return {"email": "dee@example.test", "type": "imap", "host": "imap.example.test", "credential": TYPED, **fields}


def test_a_draft_tries_what_was_typed_where_it_was_typed():
    assert throwaway_account(draft(port=143, starttls=True)) == {
        "email": "dee@example.test", "kind": "imap", "host": "imap.example.test", "port": 143, "starttls": True,
        "strip_spaces": False, "trash": "Trash", "credential": Typed("typed-password")}


def test_what_a_draft_tries_can_never_be_written_to_a_file_or_the_keychain(data_dir, fake_keychain):
    acct = throwaway_account(draft())
    assert "typed-password" not in repr(acct)
    with pytest.raises(TypeError):
        json.dumps(acct)
    with pytest.raises(TypeError):
        config_file.ConfigFile().write({"accounts": [acct]})
    assert not (data_dir / "mailmind.config.json").exists() and fake_keychain.items() == {}


@pytest.mark.parametrize("credential", [
    {"style": "op", "ref": "op://Private/Dee/password"},
    {"style": "env", "var": "MAILMIND_TEST_DEE_PASSWORD"},
    {"style": "proton_bridge"},
    {"op": "op://Private/Dee/password"},
    {"plain": "stored-as-the-file-holds-it"},
    {"keychain": "account:dee@example.test"},
    {"style": "plain", "value": "typed-password"},
    {"style": "keychain", "value": 123},
    {"style": "keychain"},
    {"style": "keychain", "value": ""},
    {},
    None,
], ids=str)
def test_a_draft_naming_anything_but_a_typed_password_is_refused(credential):
    with pytest.raises(DraftRefused, match=SAVE_FIRST):
        throwaway_account(draft(credential=credential))


@pytest.mark.xfail(strict=True, raises=AttributeError,
                   reason="throwaway_account reads the credential with .get, so one that is text raises AttributeError, "
                          "and the test answers that class's name as a failed login, where any other credential that "
                          "isn't a typed password is refused")
def test_a_draft_whose_credential_is_text_is_refused():
    with pytest.raises(DraftRefused, match=SAVE_FIRST):
        throwaway_account(draft(credential="typed-password"))


def test_a_stored_secret_named_beside_a_typed_password_is_never_what_goes():
    acct = throwaway_account(draft(credential={**TYPED, "ref": "op://Private/Dee/password", "var": "HOME", "op": "x", "env": "HOME"}))
    assert acct["credential"] == Typed("typed-password")


@pytest.mark.parametrize("host", [None, ""])
def test_a_generic_account_needs_a_host_typed(host):
    with pytest.raises(DraftRefused, match="a host is required"):
        throwaway_account(draft(host=host))


def test_a_draft_with_no_host_hears_that_before_anything_about_its_credential():
    with pytest.raises(DraftRefused, match="a host is required"):
        throwaway_account({"email": "dee@example.test", "type": "imap"})


@pytest.mark.parametrize("kind, host, port, starttls", [
    ("gmail", "imap.gmail.com", 993, False),
    ("proton", "127.0.0.1", 1143, True),
    ("imap", "imap.example.test", 993, False),
])
def test_what_is_left_out_comes_from_the_type(kind, host, port, starttls):
    typed = {"email": "dee@example.test", "type": kind, "credential": TYPED}
    if kind == "imap":
        typed["host"] = host
    acct = throwaway_account(typed)
    assert (acct["host"], acct["port"], acct["starttls"]) == (host, port, starttls)


@pytest.mark.xfail(strict=True, raises=TypeError,
                   reason="TYPE_DEFAULTS.get can't look up a list or an object, so a draft whose type is one is never "
                          "tried and the test answers `TypeError` as a failed login, where a type it doesn't know is "
                          "tried with generic IMAP's defaults")
@pytest.mark.parametrize("kind", [["imap"], {"imap": True}], ids=repr)
def test_a_draft_whose_type_isnt_text_is_tried_as_generic_imap(kind):
    acct = throwaway_account(draft(type=kind))
    assert (acct["host"], acct["port"], acct["starttls"]) == ("imap.example.test", 993, False)


def test_a_host_typed_wins_over_the_types():
    assert throwaway_account(draft(type="proton", host="bridge.example.test"))["host"] == "bridge.example.test"


def test_a_refusal_says_nothing_typed():
    for bad in (draft(host=""), draft(credential={"style": "op", "ref": "op://Private/Dee/typed-password"})):
        with pytest.raises(DraftRefused) as no:
            throwaway_account(bad)
        assert "typed-password" not in str(no.value) and "dee@" not in str(no.value)
