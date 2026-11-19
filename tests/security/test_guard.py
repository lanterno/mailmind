"""The test tripwire refuses the owner's data, the network beyond this machine, and credential
helpers, and lets everything a test legitimately needs through.

Each refusal below aims at a real path or program, but the hook raises before the operation
runs, so nothing is opened, connected or started.
"""
import os
import socket
import sqlite3
import subprocess
import sys

import pytest

import guard
from mailmind.bootstrap import curation

ROOT = guard.ROOT


@pytest.fixture
def refused():
    """The refusals recorded during the test, cleared after so the autouse check passes."""
    yield guard.violations
    guard.violations.clear()


@pytest.mark.parametrize("name", ["mailmind.db", "mailmind.db-wal", "mailmind.config.json",
                                  "mailmind_rules.json", "mailmind_views.json", "mailmind_snippets.json",
                                  "sweep_audit.jsonl", "mailmind.log", "mailmind.log.1", "MAILMIND.DB", ".cfg-x.tmp",
                                  ".snippets-x.tmp"])
def test_opening_the_owners_data_is_refused(name, refused):
    with pytest.raises(guard.Tripwire):
        open(os.path.join(ROOT, name), "rb")
    assert refused


def test_a_relative_path_to_the_owners_data_is_refused(refused, monkeypatch):
    monkeypatch.chdir(ROOT)
    with pytest.raises(guard.Tripwire):
        open("mailmind_rules.json")
    assert refused


def test_opening_the_owners_database_with_sqlite_is_refused(refused):
    with pytest.raises(guard.Tripwire):
        sqlite3.connect(os.path.join(ROOT, "mailmind.db"))
    with pytest.raises(guard.Tripwire):
        sqlite3.connect(f"file:{ROOT}/mailmind.db?mode=ro", uri=True)
    assert len(refused) == 2


def test_opening_the_owners_database_with_the_encrypted_driver_is_refused(refused):
    """It raises no audit event of its own, so the tripwire wraps its connect, and so MailMind's own way in."""
    from mailmind.platform.db import database, driver
    with pytest.raises(guard.Tripwire):
        driver.connect(os.path.join(ROOT, "mailmind.db"))
    with pytest.raises(guard.Tripwire):
        driver.connect(f"file:{ROOT}/mailmind.db?mode=ro", uri=True)
    with pytest.raises(guard.Tripwire):
        database.keyed(os.path.join(ROOT, "mailmind.db"), read_only=True)
    assert len(refused) == 4        # MailMind's way in, twice: reading the file's first bytes, then connecting


def test_renaming_onto_or_deleting_the_owners_data_is_refused(tmp_path, refused):
    src = tmp_path / "x.json"
    src.write_text("{}")
    with pytest.raises(guard.Tripwire):
        os.replace(src, os.path.join(ROOT, "mailmind.config.json"))
    with pytest.raises(guard.Tripwire):
        os.remove(os.path.join(ROOT, "reconcile_audit.jsonl"))
    assert src.exists() and len(refused) == 2


def test_a_name_given_with_a_directory_is_judged_where_it_is_not_where_the_test_runs(tmp_path, refused, monkeypatch):
    """shutil.rmtree removes each entry by name relative to its directory (`dir_fd`): a temp home's database goes even
    while the test runs at the repo root, and a guarded directory's doesn't, wherever the test runs."""
    home = tmp_path / "home"
    (home / "backups").mkdir(parents=True)
    (home / "mailmind.db").write_text("")
    (home / "backups" / "mailmind.log").write_text("")
    monkeypatch.chdir(ROOT)
    import shutil
    shutil.rmtree(home)
    assert not home.exists() and not refused
    guarded = tmp_path / "guarded"
    guarded.mkdir()
    (guarded / "mailmind.db").write_text("")
    guard.protect(str(guarded))
    monkeypatch.chdir(tmp_path)
    fd = os.open(guarded, os.O_RDONLY)
    try:
        with pytest.raises(guard.Tripwire):
            os.remove("mailmind.db", dir_fd=fd)
        with pytest.raises(guard.Tripwire):
            os.rename("mailmind.db", "moved.db", src_dir_fd=fd, dst_dir_fd=fd)
    finally:
        os.close(fd)
    assert (guarded / "mailmind.db").exists() and len(refused) == 2


def test_a_relative_name_given_no_directory_is_judged_where_the_test_runs(tmp_path, refused, monkeypatch):
    """A call given no directory raises its audit event with -1 for one, which names no descriptor: the stand-in keychain
    kept in a file named relative to where the test runs saves through `os.replace` as any other file does."""
    import fake_keychain
    monkeypatch.chdir(tmp_path)
    assert guard._at("keychain.json", -1) == "keychain.json"
    monkeypatch.setenv(fake_keychain.VARIABLE, "keychain.json")
    kept = fake_keychain.FakeKeychain()
    kept.add("account:ada@example.test", "app-password", replace=True)
    assert kept.items() == {"account:ada@example.test": "app-password"} and not refused
    assert (tmp_path / "keychain.json").exists()
    # Kept nowhere again before the test leaves for the repo, where emptying the stand-in as it ends would write it.
    monkeypatch.delenv(fake_keychain.VARIABLE)
    (tmp_path / "x.json").write_text("{}")
    monkeypatch.chdir(ROOT)
    with pytest.raises(guard.Tripwire):
        os.replace(os.path.join(tmp_path, "x.json"), "mailmind.config.json")
    assert len(refused) == 1


def test_reading_the_owners_bridge_password_file_is_refused(refused):
    """The Bridge password's last source, read only when the Keychain holds none: a test that reaches it fails, whether or
    not this computer has one. The Python server the served suites start reads one in its own data directory instead."""
    from mailmind.accounts.adapters import secrets
    with pytest.raises(guard.Tripwire):
        open(os.path.expanduser("~/.config/mailmind/proton_bridge_pass"), "rb")
    with pytest.raises(guard.Tripwire):
        secrets.BRIDGE_FILE.read_text()
    assert len(refused) == 2


def test_a_swallowed_refusal_is_still_recorded(refused):
    try:
        open(os.path.join(ROOT, "mailmind_views.json"))
    except Exception:
        pass
    assert refused == [f"open {os.path.join(ROOT, 'mailmind_views.json')!r}"]


def test_a_program_started_with_a_loud_guard_says_a_swallowed_refusal_as_it_happens():
    """As the server the demo starts installs it: no test reads its record, so what it prints must hold it."""
    code = (f"import socket, sys; sys.path[:0] = [{os.path.join(ROOT, 'tests', 'support')!r}, {ROOT!r}]; import guard; "
            "guard.install(loud=True)\n"
            "try: socket.socket().connect(('127.0.0.1', 11434))\n"
            "except OSError: pass\n"
            "print(guard.violations)")
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert (done.returncode, done.stderr) == (0, "Tripwire: connect to ('127.0.0.1', 11434)\n"), done.stderr
    assert done.stdout == "[\"connect to ('127.0.0.1', 11434)\"]\n"


def test_a_module_path_left_pointing_at_the_repo_is_caught(refused):
    from mailmind.bootstrap.mailbox import store
    with pytest.raises(guard.Tripwire):                               # no data_dir here; refused whether
        store.conn()                                                  # or not the file exists in this checkout
    assert refused == [f"open {os.path.join(ROOT, 'mailmind.db')!r}"]          # before SQLite is asked to, and nothing swallows it


@pytest.mark.parametrize("host", ["192.0.2.1", "2001:db8::1"])
def test_connecting_beyond_this_machine_is_refused(host, refused):
    s = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET)
    try:
        with pytest.raises(guard.Tripwire):
            s.connect((host, 443))
    finally:
        s.close()
    assert refused


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_connecting_to_a_model_server_on_this_machine_is_refused(host, refused):
    """Ollama, LM Studio and the rest may be running here, with a person's own models: a test asks a fake on its own port."""
    for port in sorted(guard.MODEL_SERVER_PORTS):
        s = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET)
        try:
            with pytest.raises(guard.Tripwire):
                s.connect((host, port))
        finally:
            s.close()
    assert len(refused) == len(guard.MODEL_SERVER_PORTS)


def test_the_model_servers_ports_are_those_the_app_finds_servers_on():
    from urllib.parse import urlsplit
    from mailmind.assistant.domain.providers import LOCAL_SERVERS, PROVIDERS
    assert guard.MODEL_SERVER_PORTS == {urlsplit(url).port for _, url in LOCAL_SERVERS}
    assert urlsplit(PROVIDERS["local"].base_url).port in guard.MODEL_SERVER_PORTS


def test_a_fake_on_a_port_of_its_own_on_this_machine_is_reached(refused):
    with socket.create_server(("127.0.0.1", 0)) as server, socket.create_connection(server.getsockname(), timeout=2):
        pass
    assert refused == []


def test_connecting_to_a_remote_name_is_refused_before_the_lookup(refused):
    """The way imaplib, smtplib and urllib connect: getaddrinfo first."""
    with pytest.raises(guard.Tripwire):
        socket.create_connection(("imap.example.test", 993), timeout=1)
    with pytest.raises(guard.Tripwire):
        socket.gethostbyname("api.anthropic.com")
    assert refused == ["DNS lookup of 'imap.example.test'", "DNS lookup of 'api.anthropic.com'"]


@pytest.mark.parametrize("cmd", [["claude", "--help"], ["/usr/local/bin/op", "read", "op://x/y"],
                                 ["security", "find-generic-password"]])
def test_starting_the_claude_cli_or_a_credential_helper_is_refused(cmd, refused):
    with pytest.raises(guard.Tripwire):
        subprocess.run(cmd, capture_output=True)
    assert refused


def test_a_shell_command_that_starts_the_claude_cli_is_refused(refused):
    with pytest.raises(guard.Tripwire):
        subprocess.run("echo hi && claude -p x", shell=True, capture_output=True)
    assert refused


def test_what_tests_need_is_allowed(tmp_path):
    db = sqlite3.connect(tmp_path / "mailmind.db")
    db.execute("CREATE TABLE t(x)")
    db.close()
    (tmp_path / "sweep_audit.jsonl").write_text("{}\n")
    open(os.path.join(ROOT, "web", "index.html"), "rb").close()           # source files are fine
    srv = socket.create_server(("127.0.0.1", 0))
    socket.create_connection(srv.getsockname()).close()
    srv.close()
    socket.getaddrinfo("localhost", 80)
    r = subprocess.run([sys.executable, "-c", "print('op claude security')"], capture_output=True, text=True)
    assert r.stdout.strip() == "op claude security"                    # arguments aren't programs
    assert not guard.violations


def test_data_dir_is_the_data_directory_every_module_uses(data_dir):
    import json
    from mailmind.bootstrap.accounts import config
    from mailmind.bootstrap.curation import rules
    from mailmind.bootstrap.mailbox import store
    from mailmind.bootstrap.compose import snippets
    from mailmind.bootstrap.discovery import views
    from mailmind.mailbox.adapters import reconcile_trail
    from mailmind.platform import settings
    from mailmind.platform.db.uow import UnitOfWork
    assert settings.current().home == str(data_dir)
    c = store.conn()
    try:
        db = c.execute("PRAGMA database_list").fetchone()["file"]
    finally:
        c.close()
    assert os.path.realpath(db) == os.path.realpath(data_dir / "mailmind.db")
    config.save({"accounts": []})
    rules.add("lin@example.test", "keep")
    views.add("From Lin", "from:lin")
    snippets.post({"verb": "save", "trigger": "hi", "text": "Hello"})
    curation.AUDIT.swept("ada@example.test", [])
    curation.AUDIT.cleaned_up("lin@example.test", "lin@example.test", 0, "ok")
    with UnitOfWork().write() as c:
        reconcile_trail.pruned(c, [{"account": "ada@example.test", "live": 1, "stored": 2, "ghosts": 1, "pruned": 1,
                                    "error": None, "folders": 1}])
    assert curation.classify("lin@example.test", "hi")[0] == "personal/protected"     # the rules just written
    assert json.loads((data_dir / "mailmind.config.json").read_text()) == {"accounts": []}
    assert sorted(e["kind"] for e in curation.AUDIT.read()["events"]) == ["reconcile", "sender_cleanup"]
    assert sorted(p.name for p in data_dir.iterdir() if not p.name.startswith("mailmind.db")) == [
        "mailmind.config.json"]


def test_the_directory_mailmind_home_names_is_guarded_too(tmp_path, refused):
    home = tmp_path / "home"
    home.mkdir()
    (home / "notes.txt").write_text("")
    guard.protect(str(home))
    with pytest.raises(guard.Tripwire):
        open(home / "mailmind.config.json")
    open(home / "notes.txt").close()                                  # only the data files
    assert refused == [f"open {str(home / 'mailmind.config.json')!r}"]


def test_the_tests_never_inherit_a_data_directory():
    from mailmind.platform import settings
    assert "MAILMIND_HOME" not in os.environ
    assert os.path.realpath(settings.from_env().home) == ROOT        # what a test without data_dir gets


def test_no_module_builds_a_data_path_beside_its_source():
    """Data paths come from mailmind.platform.settings, so moving the data directory moves them all."""
    import re
    found = set()
    for fn in sorted(os.listdir(ROOT)):
        if fn.endswith(".py") and not fn.startswith("test"):
            src = open(os.path.join(ROOT, fn), encoding="utf-8").read()
            for attr in re.findall(r"^([A-Z_][A-Z_0-9]*) = os\.path\.join\(", src, re.M):
                found.add((fn[:-3], attr))
    assert not found, sorted(found)


def test_a_test_keeps_and_reads_its_secrets_in_the_stand_in_never_the_login_keychain(fake_keychain):
    from mailmind.platform import keychain
    assert keychain.use(fake_keychain) is fake_keychain is guard.KEYCHAIN
    keychain.put("account:ada@example.test", "app-password")
    assert fake_keychain.items() == {"account:ada@example.test": "app-password"}


def test_a_program_started_with_the_guard_has_the_stand_in_and_shares_it_through_the_file_named(tmp_path, monkeypatch):
    """As `serve` does in a test, and the demo's server: what one process keeps, another started with it reads."""
    import fake_keychain
    from mailmind.platform import keychain
    monkeypatch.setenv(fake_keychain.VARIABLE, str(tmp_path / "keychain.json"))
    keychain.put("account:ada@example.test", "app-password")
    code = (f"import sys; sys.path[:0] = [{os.path.join(ROOT, 'tests', 'support')!r}, {ROOT!r}]; import guard; guard.install(); "
            "from mailmind.platform import keychain; print(type(keychain._backend).__name__, keychain.find('account:ada@example.test')); "
            "keychain.put('anthropic-api-key', 'sk-ant-from-the-child')")
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert (done.returncode, done.stdout) == (0, "FakeKeychain app-password\n"), done.stderr
    assert keychain.find("anthropic-api-key") == "sk-ant-from-the-child"
    assert (tmp_path / "keychain.json").stat().st_mode & 0o777 == 0o600
