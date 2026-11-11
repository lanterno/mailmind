"""`python -m mailmind accounts materialize [WHO]` (Directory.materialize): each credential kept by reference is written
into the file as the password it names, so the file stands on its own. What it can't read stays as it was, and what it
prints names accounts, never a secret."""
import json

import pytest

from mailmind.accounts.adapters import config_file
from mailmind.accounts.application.directory import Directory

ADA, BEA, CY, DEE = "ada@example.test", "bea@example.test", "cy@example.test", "dee@example.test"
ACCOUNTS = [
    {"email": ADA, "type": "gmail", "credential": {"op": "op://Private/Ada/password"}},
    {"email": BEA, "type": "imap", "host": "imap.example.test", "credential": {"env": "BEA_PASSWORD"}},
    {"email": CY, "type": "imap", "host": "imap.example.test", "credential": {"plain": "cy-secret"}},
    {"email": DEE, "type": "proton", "credential": {"proton_bridge": True}},
]


class _Secrets:
    def __init__(self, refusing=()):
        self.refusing = set(refusing)
        self.asked = []

    def resolve(self, cred, *, who, strip=False):
        self.asked.append((who, strip))
        if who in self.refusing:
            raise RuntimeError(f"no credential could be read for {who}")
        return who.partition("@")[0] + "-secret"

    def evict(self, cred):
        pass


@pytest.fixture
def config(data_dir):
    path = data_dir / "mailmind.config.json"

    def make(secrets, accounts=ACCOUNTS):
        directory = Directory(config_file.ConfigFile(), secrets, lambda: None)
        directory.save({"accounts": [dict(a) for a in accounts]})
        return directory
    make.path = path
    make.stored = lambda: {a["email"]: a["credential"] for a in json.loads(path.read_text())["accounts"]}
    return make


def test_every_credential_by_reference_is_written_in_as_its_password(config, capsys):
    config(_Secrets()).materialize()
    assert config.stored() == {ADA: {"plain": "ada-secret"}, BEA: {"plain": "bea-secret"}, CY: {"plain": "cy-secret"},
                               DEE: {"plain": "dee-secret"}}
    assert capsys.readouterr().out == (f"  materialized {ADA}\n  materialized {BEA}\n  materialized {DEE}\n"
                                       "done — 3 account(s) now self-contained in mailmind.config.json\n")


def test_one_account_named_is_the_only_one_written(config, capsys):
    secrets = _Secrets()
    config(secrets).materialize(BEA)
    assert config.stored() == {**{a["email"]: a["credential"] for a in ACCOUNTS}, BEA: {"plain": "bea-secret"}}
    assert [who for who, _ in secrets.asked] == [BEA]


def test_a_gmail_app_password_is_read_without_its_spaces_unless_the_account_says(config):
    secrets = _Secrets()
    accounts = [*ACCOUNTS, {"email": "eve@example.test", "type": "gmail", "strip_spaces": False, "credential": {"env": "EVE"}}]
    config(secrets, accounts).materialize()
    assert secrets.asked == [(ADA, True), (BEA, False), (DEE, False), ("eve@example.test", False)]


def test_one_that_cant_be_read_is_skipped_and_kept_as_it_was(config, capsys):
    config(_Secrets(refusing={ADA})).materialize()
    assert config.stored()[ADA] == {"op": "op://Private/Ada/password"}
    assert config.stored()[BEA] == {"plain": "bea-secret"}
    assert f"  ! skip {ADA}: no credential could be read for {ADA}\n" in capsys.readouterr().out


def test_with_nothing_to_write_the_file_is_left_alone(config, capsys):
    directory = config(_Secrets(), [ACCOUNTS[2]])
    before = config.path.stat().st_mtime_ns, config.path.stat().st_ino
    directory.materialize()
    assert (config.path.stat().st_mtime_ns, config.path.stat().st_ino) == before
    assert capsys.readouterr().out == "done — 0 account(s) now self-contained in mailmind.config.json\n"


def test_what_it_prints_holds_no_secret(config, capsys):
    config(_Secrets(refusing={DEE})).materialize()
    out = capsys.readouterr().out
    assert "secret" not in out
