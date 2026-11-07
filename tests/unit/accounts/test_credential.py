"""mailmind.accounts.domain.credential: the one reader of the styles the file may name, and what the API may show of one."""
import pytest

from mailmind.accounts.adapters import secrets
from mailmind.accounts.domain.credential import CredentialRef, credential_from


@pytest.mark.parametrize("stored, style, mask", [
    ({"plain": "hunter2"}, "plain", {"style": "plain", "set": True}),
    ("hunter2", "plain", {"style": "plain", "set": True}),
    ({"plain": ""}, "plain", {"style": "plain", "set": False}),
    ("", "plain", {"style": "plain", "set": False}),
    ({"op": "op://V/I/F"}, "op", {"style": "op", "set": True, "ref": "op://V/I/F"}),
    ({"op": ["op://V/I/A", "op://V/I/B"]}, "op", {"style": "op", "set": True, "ref": ["op://V/I/A", "op://V/I/B"]}),
    ({"op": ""}, "op", {"style": "op", "set": False, "ref": ""}),
    ({"env": "DEE_PASSWORD"}, "env", {"style": "env", "set": True, "var": "DEE_PASSWORD"}),
    ({"proton_bridge": True}, "proton_bridge", {"style": "proton_bridge", "set": True}),
    ({"proton_bridge": False}, "unknown", {"style": "unknown", "set": False}),
    ({"keychain": "dee"}, "unknown", {"style": "unknown", "set": False}),
    ({}, None, {"style": None, "set": False}),
    (None, None, {"style": None, "set": False}),
    (7, None, {"style": None, "set": False}),
], ids=repr)
def test_each_style_reads_and_masks(stored, style, mask):
    ref = CredentialRef.parse(stored)
    assert ref.style == style
    assert ref.masked() == mask


def test_a_password_never_shows_in_a_mask_or_a_repr():
    ref = CredentialRef.parse({"plain": "hunter2"})
    assert "hunter2" not in repr(ref) and "hunter2" not in str(ref.masked())


@pytest.mark.parametrize("named, op_refs", [("op://V/I/F", ["op://V/I/F"]), (["op://V/I/A", "op://V/I/B"], ["op://V/I/A", "op://V/I/B"])])
def test_op_refs_are_tried_in_order(named, op_refs):
    assert CredentialRef.parse({"op": named}).op_refs == op_refs


@pytest.mark.parametrize("stored, style", [
    ({"op": "op://V/I/F", "env": "DEE_PASSWORD"}, "env"),
    ({"op": "op://V/I/F", "plain": "hunter2"}, "plain"),
    ({"op": "op://V/I/F", "proton_bridge": True}, "proton_bridge"),
    ({"op": "op://V/I/F", "proton_bridge": False}, "op"),
], ids=repr)
def test_an_entry_naming_two_shows_the_one_a_login_uses(stored, style, monkeypatch):
    monkeypatch.setenv("DEE_PASSWORD", "from-env")
    monkeypatch.setenv("MAILMIND_PROTON_BRIDGE_PASS", "from-bridge")
    monkeypatch.setattr(secrets, "_bridge", {})
    monkeypatch.setattr(secrets, "_op_read", lambda ref, strip: "from-op")
    assert CredentialRef.parse(stored).style == style
    assert secrets.resolve(stored, who="dee") == {"plain": "hunter2", "env": "from-env", "proton_bridge": "from-bridge", "op": "from-op"}[style]


@pytest.mark.parametrize("stored", [None, {}, "", {"keychain": "dee"}, {"proton_bridge": False}], ids=repr)
def test_no_style_resolves_to_a_refusal_naming_no_secret(stored):
    with pytest.raises(RuntimeError, match="no credential configured for dee"):
        secrets.resolve(stored, who="dee")


def test_an_empty_plain_is_what_the_file_says():
    assert secrets.resolve({"plain": ""}, who="dee") == ""


def test_evict_drops_each_of_a_credentials_reads_and_no_others(monkeypatch):
    monkeypatch.setattr(secrets, "_op_cache", {"op://V/I/A": "a", "op://V/I/B": "b", "op://V/I/C": "c"})
    secrets.evict({"op": ["op://V/I/A", "op://V/I/B"]})
    assert list(secrets._op_cache) == ["op://V/I/C"]
    secrets.evict({"plain": "op://V/I/C"})
    assert list(secrets._op_cache) == ["op://V/I/C"]


@pytest.mark.parametrize("asked, stored", [
    ({"style": "plain", "value": "hunter2"}, {"plain": "hunter2"}),
    ({"style": "op", "ref": "op://V/I/F"}, {"op": "op://V/I/F"}),
    ({"style": "op", "ref": ["op://V/I/A", "op://V/I/B"]}, {"op": ["op://V/I/A", "op://V/I/B"]}),
    ({"style": "env", "var": " DEE_PASSWORD "}, {"env": "DEE_PASSWORD"}),
    ({"style": "proton_bridge"}, {"proton_bridge": True}),
], ids=repr)
def test_a_request_writes_the_style_it_names(asked, stored):
    assert credential_from(asked) == stored


@pytest.mark.parametrize("asked", [None, {}, {"style": "keychain"}, {"value": "hunter2"}], ids=repr)
def test_a_request_naming_no_style_keeps_what_is_there(asked):
    assert credential_from(asked) is None


@pytest.mark.parametrize("asked, why", [
    ({"style": "plain"}, "password required"),
    ({"style": "plain", "value": ""}, "password required"),
    ({"style": "op", "ref": "  "}, "1Password reference required"),
    ({"style": "op"}, "1Password reference required"),
    ({"style": "env", "var": " "}, "environment variable name required"),
], ids=repr)
def test_a_request_may_not_blank_a_credential(asked, why):
    with pytest.raises(ValueError, match=why):
        credential_from(asked)
