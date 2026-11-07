"""The Account's invariants (mailmind.accounts.domain.account, Directory): each email once, both endpoints on an enabled
account, certificate checks skipped only on loopback, and no secret but through its reference. They hold where a change
enters. What a file already holds is the owner's, so it is read however it stands."""
import json

import pytest

from mailmind.accounts.adapters import config_file, secrets
from mailmind.accounts.application.directory import Directory
from mailmind.accounts.domain import account
from mailmind.accounts.domain.account import imap_endpoint, smtp_endpoint

ADA = {"email": "ada@example.test", "type": "imap", "host": "imap.example.test", "credential": {"plain": "hunter2"}}
TYPED = {"style": "plain", "value": "hunter2"}


@pytest.fixture
def directory(data_dir):
    d = Directory(config_file.ConfigFile(), secrets, lambda: None)
    d.save({"accounts": [dict(ADA)]})
    return d


def stored(data_dir):
    return json.loads((data_dir / "mailmind.config.json").read_text())["accounts"]


# -- the endpoints -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind, imap, smtp", [
    ("gmail", ("imap.gmail.com", 993, False), ("smtp.gmail.com", 587, True)),
    ("proton", ("127.0.0.1", 1143, True), ("127.0.0.1", 1025, True)),
])
def test_what_an_entry_leaves_out_comes_from_its_type(kind, imap, smtp):
    entry = {"email": "ada@example.test", "type": kind}
    assert (imap_endpoint(entry).host, imap_endpoint(entry).port, imap_endpoint(entry).starttls) == imap
    assert (smtp_endpoint(entry).host, smtp_endpoint(entry).port, smtp_endpoint(entry).starttls) == smtp


def test_a_generic_accounts_smtp_is_its_imap_host_unless_it_names_one():
    assert smtp_endpoint(ADA).host == "imap.example.test"
    assert smtp_endpoint({**ADA, "smtp_host": "smtp.example.test"}).host == "smtp.example.test"
    assert smtp_endpoint({**ADA, "smtp_host": ""}).host == "imap.example.test"


def test_the_app_and_the_api_read_the_same_endpoints():
    entry = {**ADA, "smtp_port": 2525, "port": 143, "starttls": True}
    used, shown = account.normalized(entry), account.masked_row(entry)
    for key in ("host", "port", "starttls", "smtp_host", "smtp_port", "smtp_starttls"):
        assert used[key] == shown[key]


def test_an_account_the_file_gives_no_host_is_not_used():
    assert account.normalized({"email": "ada@example.test", "type": "imap"}) is None
    assert account.normalized({"email": "ada@example.test", "type": "gmail", "host": ""}) is None


# -- each email once ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("email", ["ada@example.test", "ADA@Example.Test", "  ada@example.test "])
def test_an_address_already_there_is_refused_however_it_is_written(directory, data_dir, email):
    with pytest.raises(ValueError, match="already exists"):
        directory.add_account({"email": email, "type": "imap", "host": "other.example.test", "credential": TYPED})
    assert [a["email"] for a in stored(data_dir)] == ["ada@example.test"]


def test_an_edit_cannot_rename_an_account_onto_another(directory, data_dir):
    directory.add_account({"email": "bea@example.test", "type": "imap", "host": "imap.example.test", "credential": TYPED})
    directory.update_account("bea@example.test", {"email": "ada@example.test", "alias": "Bea"})
    assert [a["email"] for a in stored(data_dir)] == ["ada@example.test", "bea@example.test"]


# -- both endpoints ----------------------------------------------------------------------------------------------

def test_a_generic_account_needs_a_host_to_be_added(directory):
    with pytest.raises(ValueError, match="a host is required"):
        directory.add_account({"email": "bea@example.test", "type": "imap", "credential": TYPED})


def test_an_account_with_a_type_of_its_own_can_lose_that_type_only_by_naming_a_host(directory):
    directory.add_account({"email": "bea@example.test", "type": "gmail", "credential": TYPED})
    with pytest.raises(ValueError, match="a host is required"):
        directory.update_account("bea@example.test", {"type": "imap"})
    assert directory.update_account("bea@example.test", {"type": "imap", "host": "imap.example.test"})["type"] == "imap"


def test_an_edit_cannot_blank_a_host(directory, data_dir):
    directory.update_account("ada@example.test", {"host": "", "smtp_host": None})
    assert stored(data_dir)[0]["host"] == "imap.example.test"


def test_a_refused_edit_changes_nothing_in_the_file(directory, data_dir):
    before = stored(data_dir)
    with pytest.raises(ValueError):
        directory.update_account("ada@example.test", {"port": 70000, "alias": "Ada"})
    assert stored(data_dir) == before


# -- secrets -----------------------------------------------------------------------------------------------------

def test_what_the_api_shows_of_an_account_holds_no_secret(directory):
    rows = [directory.masked_rows()[0], directory.add_account({"email": "bea@example.test", "type": "imap", "host": "h.example.test", "credential": TYPED}),
            directory.update_account("ada@example.test", {"credential": {"style": "plain", "value": "hunter3"}})]
    assert "hunter" not in str(rows)


def test_an_account_as_the_app_reads_it_holds_the_reference_not_what_it_names(directory, monkeypatch):
    monkeypatch.setenv("BEA_PASSWORD", "hunter2")
    directory.add_account({"email": "bea@example.test", "type": "imap", "host": "h.example.test",
                           "credential": {"style": "env", "var": "BEA_PASSWORD"}})
    bea = next(a for a in directory.accounts() if a["email"] == "bea@example.test")
    assert bea["credential"] == {"env": "BEA_PASSWORD"}
    assert directory.resolve_credential(bea) == "hunter2"


# -- a write, not a read -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("fields, why", [
    ({"host": "two words"}, "host must be a host name"),
    ({"host": "imap.example.test\r\nX"}, "host must be a host name"),
    ({"host": 123}, "host must be a host name"),
    ({"smtp_host": "two words"}, "SMTP host must be a host name"),
    ({"smtp_host": 123}, "SMTP host must be a host name"),
    ({"smtp_port": 70000}, "SMTP port must be 1–65535"),
    ({"smtp_port": 0}, "SMTP port must be 1–65535"),
], ids=repr)
def test_a_write_may_not_leave_an_endpoint_no_connection_could_reach(directory, data_dir, fields, why):
    with pytest.raises(ValueError, match=why):
        directory.add_account({"email": "bea@example.test", "type": "imap", "host": "imap.example.test", "credential": TYPED, **fields})
    with pytest.raises(ValueError, match=why):
        directory.update_account("ada@example.test", fields)
    assert stored(data_dir) == [ADA]


def test_a_gmail_account_is_held_to_it_as_well(directory):
    with pytest.raises(ValueError, match="host must be a host name"):
        directory.add_account({"email": "bea@example.test", "type": "gmail", "host": "two words", "credential": TYPED})


@pytest.mark.parametrize("held", [{"smtp_port": 70000}, {"smtp_host": "two words"}, {"host": "two words"}, {"host": 123}], ids=repr)
def test_what_the_file_already_held_doesnt_stop_an_edit_of_something_else(directory, data_dir, held):
    directory.save({"accounts": [{**ADA, **held}]})
    assert directory.update_account("ada@example.test", {"alias": "Ada"})["alias"] == "Ada"
    assert stored(data_dir)[0] == {**ADA, **held, "alias": "Ada"}


@pytest.mark.parametrize("held", [{"smtp_port": 70000}, {"smtp_host": "two words"}, {"host": "two words"}, {"host": 123}], ids=repr)
def test_what_the_file_already_held_reads_as_it_stands(directory, held):
    directory.save({"accounts": [{**ADA, **held}]})
    assert [a["email"] for a in directory.accounts()] == ["ada@example.test"]
    assert directory.masked_rows()[0]["email"] == "ada@example.test"


def test_an_entry_the_file_gives_no_address_is_not_used_and_stops_nothing_else(directory):
    directory.save({"accounts": [{"type": "imap", "host": "imap.example.test", "credential": {"plain": "hunter2"}}, dict(ADA)]})
    assert [a["email"] for a in directory.accounts()] == ["ada@example.test"]
    assert [r["email"] for r in directory.masked_rows()] == [None, "ada@example.test"]
