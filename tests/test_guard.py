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

ROOT = guard.ROOT


@pytest.fixture
def refused():
    """The refusals recorded during the test, cleared after so the autouse check passes."""
    yield guard.violations
    guard.violations.clear()


@pytest.mark.parametrize("name", ["mailmind.db", "mailmind.db-wal", "mailmind.config.json",
                                  "mailmind_rules.json", "mailmind_views.json", "sweep_audit.jsonl",
                                  "MAILMIND.DB", ".cfg-x.tmp"])
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


def test_renaming_onto_or_deleting_the_owners_data_is_refused(tmp_path, refused):
    src = tmp_path / "x.json"
    src.write_text("{}")
    with pytest.raises(guard.Tripwire):
        os.replace(src, os.path.join(ROOT, "mailmind.config.json"))
    with pytest.raises(guard.Tripwire):
        os.remove(os.path.join(ROOT, "reconcile_audit.jsonl"))
    assert src.exists() and len(refused) == 2


def test_a_swallowed_refusal_is_still_recorded(refused):
    try:
        open(os.path.join(ROOT, "mailmind_views.json"))
    except Exception:
        pass
    assert refused == [f"open {os.path.join(ROOT, 'mailmind_views.json')!r}"]


def test_a_module_path_left_pointing_at_the_repo_is_caught(refused):
    import store
    with pytest.raises(guard.Tripwire):                               # no data_dir here; refused whether
        store.conn()                                                  # or not the file exists in this checkout
    assert refused == [f"sqlite3.connect {os.path.join(ROOT, 'mailmind.db')!r}"]


@pytest.mark.parametrize("host", ["192.0.2.1", "2001:db8::1"])
def test_connecting_beyond_this_machine_is_refused(host, refused):
    s = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET)
    try:
        with pytest.raises(guard.Tripwire):
            s.connect((host, 443))
    finally:
        s.close()
    assert refused


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
    open(os.path.join(ROOT, "dashboard.html"), "rb").close()           # source files are fine
    srv = socket.create_server(("127.0.0.1", 0))
    socket.create_connection(srv.getsockname()).close()
    srv.close()
    socket.getaddrinfo("localhost", 80)
    r = subprocess.run([sys.executable, "-c", "print('op claude security')"], capture_output=True, text=True)
    assert r.stdout.strip() == "op claude security"                    # arguments aren't programs
    assert not guard.violations


def test_data_dir_is_the_data_directory_every_module_uses(data_dir):
    import json
    import config
    import manage
    import mailclass
    import rules
    import server
    import store
    import views
    from mailmind.platform import settings
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
    manage.audit("ada@example.test", [])
    server._cleanup_audit("lin@example.test", "lin@example.test", 0, "ok")
    (data_dir / "reconcile_audit.jsonl").write_text(json.dumps({"kind": "reconcile", "ts": "t"}) + "\n")
    assert mailclass.classify("lin@example.test", "hi")[0] == "personal/protected"     # the rules just written
    assert json.loads((data_dir / "mailmind.config.json").read_text()) == {"accounts": []}
    assert sorted(e["kind"] for e in server.read_audit()["events"]) == ["reconcile", "sender_cleanup"]
    assert sorted(p.name for p in data_dir.iterdir() if not p.name.startswith("mailmind.db")) == [
        "mailmind.config.json", "mailmind_rules.json", "mailmind_views.json", "reconcile_audit.jsonl",
        "sweep_audit.jsonl"]


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
