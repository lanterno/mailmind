"""mailmind.accounts.domain.draft: an unsaved account may only try a password typed with it, only against the host typed
with it. No server is needed to say so. test_hwf_http's route test holds that nothing reaches a server, too."""
import pytest

from mailmind.accounts.domain.draft import DraftRefused, throwaway_account

TYPED = {"style": "plain", "value": "typed-password"}
SAVE_FIRST = "save the account to test a stored secret; before then only a typed password is tried"


def draft(**fields):
    return {"email": "dee@example.test", "type": "imap", "host": "imap.example.test", "credential": TYPED, **fields}


def test_a_draft_tries_what_was_typed_where_it_was_typed():
    assert throwaway_account(draft(port=143, starttls=True)) == {
        "email": "dee@example.test", "kind": "imap", "host": "imap.example.test", "port": 143, "starttls": True,
        "strip_spaces": False, "trash": "Trash", "credential": {"plain": "typed-password"}}


@pytest.mark.parametrize("credential", [
    {"style": "op", "ref": "op://Private/Dee/password"},
    {"style": "env", "var": "MAILMIND_TEST_DEE_PASSWORD"},
    {"style": "proton_bridge"},
    {"op": "op://Private/Dee/password"},
    {"plain": "stored-as-the-file-holds-it"},
    {"style": "plain"},
    {"style": "plain", "value": ""},
    {},
    None,
], ids=str)
def test_a_draft_naming_anything_but_a_typed_password_is_refused(credential):
    with pytest.raises(DraftRefused, match=SAVE_FIRST):
        throwaway_account(draft(credential=credential))


def test_a_stored_secret_named_beside_a_typed_password_is_never_what_goes():
    acct = throwaway_account(draft(credential={**TYPED, "ref": "op://Private/Dee/password", "var": "HOME", "op": "x", "env": "HOME"}))
    assert acct["credential"] == {"plain": "typed-password"}


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


def test_a_host_typed_wins_over_the_types():
    assert throwaway_account(draft(type="proton", host="bridge.example.test"))["host"] == "bridge.example.test"


def test_a_refusal_says_nothing_typed():
    for bad in (draft(host=""), draft(credential={"style": "op", "ref": "op://Private/Dee/typed-password"})):
        with pytest.raises(DraftRefused) as no:
            throwaway_account(bad)
        assert "typed-password" not in str(no.value) and "dee@" not in str(no.value)
