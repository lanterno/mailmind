"""The secrets a person keeps in the login Keychain from Settings beside each account's own password (Directory), as the Rust
core's tests hold its own (core/crates/mm-accounts' tests/kept_secrets.rs): the Claude API key, kept in its item and named in
the file one version on, and taken out of both; the Proton Bridge's password, kept in its item, the one a login found let go,
and each account that logs in with it told it changed. A secret the Keychain can't hold is refused in its words, a Keychain
that won't is Unreachable, and either leaves the file as it was. The Keychain is the guard's stand-in."""
import json

import pytest

from mailmind.accounts.adapters import config_file, secrets
from mailmind.accounts.application.directory import Directory
from mailmind.accounts.domain import events
from mailmind.kernel.errors import Unreachable
from mailmind.platform import bus, keychain

KEY_ITEM, BRIDGE_ITEM = "anthropic-api-key", "proton-bridge-password"
OP_KEY = "op://Private/Claude/credential"


@pytest.fixture
def directory(data_dir):
    secrets.forget()
    yield Directory(config_file.ConfigFile(), secrets, lambda: None)
    secrets.forget()


def written(data_dir):
    path = data_dir / "mailmind.config.json"
    return json.loads(path.read_text()) if path.exists() else None


# -- the Claude API key ------------------------------------------------------------------------------------------

def test_the_key_is_kept_in_its_item_and_the_file_names_the_item_one_version_on(directory, data_dir, fake_keychain):
    assert not directory.api_key_set()
    directory.set_api_key("sk-ant-first")
    assert fake_keychain.items() == {KEY_ITEM: "sk-ant-first"}
    assert written(data_dir) == {"accounts": [], "anthropic_api_key": {"keychain": KEY_ITEM, "version": 1}}
    assert directory.api_key_set()
    directory.set_api_key("sk-ant-second")
    assert fake_keychain.items() == {KEY_ITEM: "sk-ant-second"}
    assert written(data_dir)["anthropic_api_key"] == {"keychain": KEY_ITEM, "version": 2}


def test_a_key_kept_in_place_of_one_1password_held_lets_go_of_what_op_read(directory, data_dir, monkeypatch):
    directory.save({"accounts": [], "anthropic_api_key": {"op": OP_KEY}, "theme": "dark"})
    monkeypatch.setitem(secrets._op_cache, OP_KEY, "sk-ant-from-op")
    directory.set_api_key("sk-ant-kept")
    assert list(written(data_dir)) == ["accounts", "anthropic_api_key", "theme"]
    assert written(data_dir)["anthropic_api_key"] == {"keychain": KEY_ITEM, "version": 1}
    assert OP_KEY not in secrets._op_cache


def test_clearing_the_key_takes_its_item_and_whatever_the_file_named_out(directory, data_dir, fake_keychain):
    directory.set_api_key("sk-ant-kept")
    directory.clear_api_key()
    assert fake_keychain.items() == {} and written(data_dir) == {"accounts": []}
    assert not directory.api_key_set()
    directory.clear_api_key()                       # nothing named, nothing kept: still done
    directory.save({"accounts": [], "anthropic_api_key": {"env": "MY_KEY"}})
    directory.clear_api_key()
    assert written(data_dir) == {"accounts": []}


@pytest.mark.parametrize("typed", ["", "sk-ant-café", "two\nlines"])
def test_a_key_the_keychain_cant_hold_is_refused_in_its_words_and_nothing_changes(directory, data_dir, fake_keychain,
                                                                                 typed):
    directory.save({"accounts": []})
    with pytest.raises(ValueError, match="A secret MailMind keeps in the Keychain is printable ASCII, and not empty"):
        directory.set_api_key(typed)
    assert fake_keychain.items() == {} and written(data_dir) == {"accounts": []}


class _Locked:
    """A Keychain that won't: locked, say."""

    def find(self, name):
        raise keychain.Refused(f"The Keychain didn't give MailMind its item {name}", 36)

    def add(self, name, secret, *, replace):
        pass

    def delete(self, name):
        raise keychain.Refused(f"The Keychain didn't take MailMind's item {name} out", 36)


@pytest.fixture
def locked(fake_keychain):
    keychain.use(_Locked())
    yield
    keychain.use(fake_keychain)


def test_a_keychain_that_wont_is_unreachable_and_the_file_is_left_as_it_was(directory, data_dir, locked):
    named = {"accounts": [], "anthropic_api_key": {"keychain": KEY_ITEM, "version": 1}}
    directory.save(named)
    with pytest.raises(Unreachable, match="The Keychain didn't keep the password. Is the login keychain locked?"):
        directory.set_api_key("sk-ant-new")
    with pytest.raises(Unreachable, match="The Keychain didn't let go of the key. Is the login keychain locked?"):
        directory.clear_api_key()
    assert written(data_dir) == named


# -- the Proton Bridge's password --------------------------------------------------------------------------------

def proton(email, credential, **more):
    return {"email": email, "type": "proton", "host": "127.0.0.1", "port": 1143, "credential": credential, **more}


@pytest.fixture
def bridged(directory):
    """Two accounts on the Bridge, one of them disabled, and one that isn't; and what Accounts said changed."""
    directory.save({"accounts": [proton("ada@example.test", {"keychain": "account:ada@example.test"}),
                                 proton("bea@example.test", {"proton_bridge": True}),
                                 proton("cy@example.test", {"proton_bridge": True}, disabled=True)]})
    heard = []
    unsubscribe = bus.subscribe(events.AccountChanged, heard.append)
    yield heard
    unsubscribe()


def test_the_bridges_password_is_kept_in_its_item_and_each_account_on_the_bridge_hears_it_changed(
        directory, data_dir, fake_keychain, bridged):
    before = written(data_dir)
    assert not directory.bridge_password_set()
    directory.set_bridge_password("bridge-first")
    assert fake_keychain.items() == {BRIDGE_ITEM: "bridge-first"} and directory.bridge_password_set()
    # Its login is let go of any hold, and IDLE logs in afresh; the disabled one waits until it is enabled.
    assert bridged == [events.AccountChanged("bea@example.test")]
    assert written(data_dir) == before                  # the file names no Bridge password
    bridged.clear()
    directory.clear_bridge_password()
    assert fake_keychain.items() == {} and not directory.bridge_password_set()
    assert bridged == [events.AccountChanged("bea@example.test")]
    assert written(data_dir) == before


def test_a_new_password_takes_the_place_of_the_one_a_login_found_and_one_taken_out_is_found_no_more(
        directory, bridged, monkeypatch, tmp_path):
    monkeypatch.setattr(secrets, "BRIDGE_FILE", tmp_path / "proton_bridge_pass")
    bea = next(a for a in directory.accounts() if a["email"] == "bea@example.test")
    directory.set_bridge_password("bridge-first")
    assert directory.resolve_credential(bea) == "bridge-first"
    directory.set_bridge_password("bridge-second")
    assert directory.resolve_credential(bea) == "bridge-second"
    directory.clear_bridge_password()
    with pytest.raises(RuntimeError, match="Proton Bridge password not configured"):
        directory.resolve_credential(bea)


@pytest.mark.parametrize("typed", ["", "café", "two\nlines"])
def test_a_bridge_password_the_keychain_cant_hold_is_refused_in_its_words(directory, fake_keychain, bridged, typed):
    with pytest.raises(ValueError, match="A secret MailMind keeps in the Keychain is printable ASCII, and not empty"):
        directory.set_bridge_password(typed)
    assert fake_keychain.items() == {} and bridged == []


def test_a_keychain_that_wont_keep_answer_or_let_go_of_the_bridges_password_is_unreachable(directory, bridged, locked):
    with pytest.raises(Unreachable, match="The Keychain didn't answer. Is the login keychain locked?"):
        directory.bridge_password_set()
    with pytest.raises(Unreachable, match="The Keychain didn't keep the password. Is the login keychain locked?"):
        directory.set_bridge_password("bridge")
    with pytest.raises(Unreachable, match="The Keychain didn't let go of the password. Is the login keychain locked?"):
        directory.clear_bridge_password()
    assert bridged == []
