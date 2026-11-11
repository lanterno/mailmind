"""A password typed into Settings (Directory): it goes to the account's own item in the login Keychain before the file is
written, and the file names the item. A new one replaces what the item held, a credential kept another way lets the item
go, and so does removing the account. A Keychain that won't leaves the file as it was. The Keychain is the guard's
stand-in."""
import json

import pytest

from mailmind.accounts.adapters import config_file, secrets
from mailmind.accounts.application.directory import Directory
from mailmind.accounts.domain.credential import PlaintextSecret
from mailmind.kernel.errors import Unreachable
from mailmind.platform import keychain

ADA, BEA = "ada@example.test", "bea@example.test"
ADAS = "account:ada@example.test"
ENTRY = {"email": ADA, "type": "imap", "host": "imap.example.test"}


def typed(password):
    return {"style": "keychain", "value": password}


@pytest.fixture
def directory(data_dir):
    return Directory(config_file.ConfigFile(), secrets, lambda: None)


def written(data_dir):
    return (data_dir / "mailmind.config.json").read_text()


def stored(data_dir):
    return {a["email"]: a["credential"] for a in json.loads(written(data_dir))["accounts"]}


def test_an_added_accounts_password_is_kept_in_its_item_and_the_file_names_the_item(directory, data_dir, fake_keychain):
    row = directory.add_account({**ENTRY, "credential": typed("hunter2")})
    assert fake_keychain.items() == {ADAS: "hunter2"}
    assert stored(data_dir) == {ADA: {"keychain": ADAS, "version": 1}}
    assert "hunter2" not in written(data_dir) and "hunter2" not in str(row)
    assert row["credential"] == {"style": "keychain", "set": True, "item": ADAS}
    assert directory.resolve_credential(directory.accounts()[0]) == "hunter2"


def test_a_new_password_replaces_what_the_item_held_and_is_a_new_version(directory, data_dir, fake_keychain):
    directory.add_account({**ENTRY, "credential": typed("hunter2")})
    assert directory.resolve_credential(directory.accounts()[0]) == "hunter2"
    directory.update_account(ADA, {"credential": typed("hunter3")})
    assert fake_keychain.items() == {ADAS: "hunter3"}
    assert stored(data_dir) == {ADA: {"keychain": ADAS, "version": 2}}
    assert directory.resolve_credential(directory.accounts()[0]) == "hunter3"


def test_an_edit_that_names_no_password_leaves_the_item_alone(directory, data_dir, fake_keychain):
    directory.add_account({**ENTRY, "credential": typed("hunter2")})
    directory.update_account(ADA, {"alias": "Ada"})
    directory.update_account(ADA, {"credential": {}})
    assert fake_keychain.items() == {ADAS: "hunter2"} and stored(data_dir)[ADA] == {"keychain": ADAS, "version": 1}


def test_a_password_kept_another_way_lets_the_item_go(directory, data_dir, fake_keychain):
    directory.add_account({**ENTRY, "credential": typed("hunter2")})
    directory.update_account(ADA, {"credential": {"style": "env", "var": "ADA_PASSWORD"}})
    assert fake_keychain.items() == {} and stored(data_dir) == {ADA: {"env": "ADA_PASSWORD"}}


def test_removing_an_account_takes_its_item_out_and_no_other(directory, fake_keychain):
    directory.add_account({**ENTRY, "credential": typed("hunter2")})
    directory.add_account({**ENTRY, "email": BEA, "credential": typed("bea-password")})
    fake_keychain.add("anthropic-api-key", "sk-ant-kept", replace=False)
    assert directory.remove_account(ADA)
    assert fake_keychain.items() == {"account:bea@example.test": "bea-password", "anthropic-api-key": "sk-ant-kept"}


def test_removing_an_account_kept_another_way_still_takes_out_an_item_left_behind(directory, fake_keychain):
    directory.add_account({**ENTRY, "credential": {"style": "env", "var": "ADA_PASSWORD"}})
    fake_keychain.add(ADAS, "left-behind", replace=False)
    assert directory.remove_account(ADA) and fake_keychain.items() == {}


def test_a_refused_add_keeps_nothing_in_the_keychain(directory, data_dir, fake_keychain):
    with pytest.raises(ValueError, match="a host is required"):
        directory.add_account({"email": ADA, "type": "imap", "credential": typed("hunter2")})
    with pytest.raises(ValueError, match="host must be a host name"):
        directory.add_account({**ENTRY, "host": "two words", "credential": typed("hunter2")})
    assert fake_keychain.items() == {} and not (data_dir / "mailmind.config.json").exists()


def test_a_refused_edit_keeps_nothing_in_the_keychain(directory, fake_keychain):
    directory.add_account({**ENTRY, "credential": typed("hunter2")})
    with pytest.raises(ValueError):
        directory.update_account(ADA, {"port": 70000, "credential": typed("hunter3")})
    assert fake_keychain.items() == {ADAS: "hunter2"}


@pytest.mark.parametrize("password", ["café-crème", "line\nbreak"])
def test_a_password_the_keychain_cant_hold_is_refused_and_never_said(directory, data_dir, fake_keychain, password):
    with pytest.raises(ValueError, match="printable ASCII") as no:
        directory.add_account({**ENTRY, "credential": typed(password)})
    assert password not in str(no.value)
    assert fake_keychain.items() == {} and not (data_dir / "mailmind.config.json").exists()


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


def test_a_keychain_that_wont_keep_a_password_is_said_and_the_file_is_left_as_it_was(directory, data_dir, locked):
    directory.save({"accounts": [{**ENTRY, "credential": {"env": "ADA_PASSWORD"}}]})
    before = written(data_dir)
    with pytest.raises(Unreachable, match="The Keychain didn't keep the password. Is the login keychain locked?"):
        directory.add_account({**ENTRY, "email": BEA, "credential": typed("hunter2")})
    with pytest.raises(Unreachable, match="didn't keep the password"):
        directory.update_account(ADA, {"credential": typed("hunter2")})
    with pytest.raises(Unreachable, match="didn't let go of the password"):
        directory.remove_account(ADA)
    assert written(data_dir) == before


def test_a_password_kept_for_an_account_the_file_held_one_for_ends_the_refusal(directory, data_dir, fake_keychain):
    directory.save({"accounts": [{**ENTRY, "credential": {"plain": "hunter2"}}]})
    with pytest.raises(PlaintextSecret, match=f"accounts password {ADA}"):
        directory.refuse_plaintext()
    directory.set_password(ADA, "hunter2")
    assert stored(data_dir) == {ADA: {"keychain": f"account:{ADA}", "version": 1}}
    directory.refuse_plaintext()


def test_a_file_that_holds_no_secret_itself_is_not_refused(directory):
    directory.save({"anthropic_api_key": "", "accounts": [{**ENTRY, "credential": {"plain": ""}},
                                                          {**ENTRY, "email": BEA, "credential": {"env": "BEA_PASSWORD"}}]})
    directory.refuse_plaintext()

