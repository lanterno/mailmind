"""`python -m mailmind db encrypt` (mailmind.platform.db.encrypt) on fictional plaintext databases in temp folders: a dry
run changes nothing and asks no Keychain; a run encrypts the database and its backups, each checked first; a database
in use, or a copy that doesn't hold what it should, is left as it was; and no report shows a row."""
import hashlib
import os
import sqlite3

import pytest

from mailmind.__main__ import main
from mailmind.platform import settings
from mailmind.platform.db import backup, database, encrypt, key, migrate

MARK = "tweed-bolt-2083"                # in every row written here; no report may show it


def plaintext_database(folder):
    """mailmind.db in `folder`, plaintext as an older MailMind left it, at the newest schema: two indexed messages and a
    stored body, written through the standard library's sqlite3 after the schema was decrypted out of a new one."""
    path = str(folder / "mailmind.db")
    made = str(folder / "made.db")
    migrate.init(made)
    c = database.keyed(made)
    try:
        c.execute("ATTACH DATABASE ? AS plain KEY ''", (path,))
        c.execute("SELECT sqlcipher_export('plain')")
        c.execute(f"PRAGMA plain.user_version={c.execute('PRAGMA user_version').fetchone()[0]}")
        c.execute("DETACH DATABASE plain")
    finally:
        c.close()
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(made + suffix):
            os.unlink(made + suffix)
    plain = sqlite3.connect(path)
    plain.execute("PRAGMA journal_mode=WAL")
    for uid in (1, 2):
        plain.execute("INSERT INTO messages(account, kind, folder, uid, message_id, subject, from_addr) "
                      "VALUES ('ada@example.test', 'imap', 'INBOX', ?, ?, ?, 'lin@example.test')",
                      (uid, f"<{uid}@example.test>", f"{MARK} {uid}"))
        plain.execute("INSERT INTO msg_fts(rowid, subject) VALUES (?, ?)", (uid, f"{MARK} {uid}"))
    plain.execute("INSERT INTO bodies(message_pk, raw) VALUES (1, ?)", (MARK.encode(),))
    plain.commit()
    plain.close()
    return path


def plaintext_backup(path):
    folder = os.path.join(os.path.dirname(path), backup.FOLDER)
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, "mailmind-v46-20261101T000000Z.db")
    src, out = sqlite3.connect(path), sqlite3.connect(dest)
    src.backup(out)
    src.close()
    out.close()
    return dest


def fingerprint(folder):
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.rglob("*") if p.is_file() and not p.name.endswith("-shm")}


def run(path, dry=False):
    lines = []
    code = encrypt.encrypt(path, say=lines.append, dry=dry)
    return code, "\n".join(lines)


def subjects(path):
    c = database.keyed(path)
    try:
        return sorted(r[0] for r in c.execute("SELECT subject FROM messages")), \
            c.execute("SELECT count(*) FROM msg_fts WHERE msg_fts MATCH ?", (f'"{MARK}"',)).fetchone()[0]
    finally:
        c.close()


@pytest.fixture
def plain(tmp_path):
    with settings.use(settings.Settings(home=str(tmp_path))):
        yield plaintext_database(tmp_path)


def test_a_dry_run_changes_nothing_asks_no_keychain_and_shows_no_row(plain, tmp_path, monkeypatch):
    plaintext_backup(plain)
    before = fingerprint(tmp_path)
    monkeypatch.setattr(key, "ensure", lambda: pytest.fail("a dry run asked for the key"))
    monkeypatch.setattr(key, "existing", lambda: pytest.fail("a dry run asked for the key"))
    code, report = run(plain, dry=True)
    assert code == encrypt.OK, report
    assert "the copy holds what the database does: yes" in report and "a run would encrypt too: 1" in report
    assert MARK not in report
    assert fingerprint(tmp_path) == before and database.plaintext(plain)


def test_a_run_encrypts_the_database_and_each_backup_and_keeps_every_row(plain, tmp_path):
    copy = plaintext_backup(plain)
    code, report = run(plain)
    assert code == encrypt.OK, report
    assert MARK not in report
    assert not database.plaintext(plain) and not database.plaintext(copy)
    assert subjects(plain) == subjects(copy) == ([f"{MARK} 1", f"{MARK} 2"], 2)
    assert not [p.name for p in tmp_path.rglob("*") if p.is_file() and MARK.encode() in p.read_bytes()]
    assert not [p.name for p in tmp_path.rglob("*") if encrypt.SUFFIX in p.name]
    assert oct(os.stat(plain).st_mode & 0o777) == "0o600"
    migrate.init(plain)                                     # and a start finds it as its own


def test_a_second_run_finds_nothing_to_do(plain):
    assert run(plain)[0] == encrypt.OK
    code, report = run(plain)
    assert code == encrypt.OK and "Nothing changed" in report


def test_a_database_in_use_is_left_as_it_was(plain, tmp_path, monkeypatch):
    monkeypatch.setattr(encrypt, "WAIT_SECONDS", 0)
    other = sqlite3.connect(plain)
    other.execute("SELECT count(*) FROM messages").fetchone()   # another process, reading it as MailMind does
    try:
        before = fingerprint(tmp_path)
        code, report = run(plain)
    finally:
        other.close()
    assert (code, "in use" in report) == (encrypt.PROBLEM, True)
    assert fingerprint(tmp_path) == before and database.plaintext(plain)


def test_a_copy_that_doesnt_hold_what_the_database_does_never_replaces_it(plain, tmp_path, monkeypatch):
    real = encrypt._state
    states = []

    def short_one(c):
        state = real(c)
        states.append(state)
        if len(states) == 2:
            state["rows"]["messages"] -= 1                  # the copy, as if it had lost a row
        return state
    monkeypatch.setattr(encrypt, "_state", short_one)
    code, report = run(plain)
    assert code == encrypt.PROBLEM and "CHANGED" in report and "left as it was" in report
    assert database.plaintext(plain)
    assert not [p.name for p in tmp_path.rglob("*") if encrypt.SUFFIX in p.name]


def test_what_an_earlier_run_left_beside_it_is_cleared_first(plain):
    with open(plain + encrypt.SUFFIX, "wb") as f:
        f.write(b"half an export")
    assert run(plain)[0] == encrypt.OK and not database.plaintext(plain)


def test_no_database_is_said_plainly(tmp_path):
    code, report = run(str(tmp_path / "mailmind.db"))
    assert code == encrypt.CANNOT_RUN and "No database" in report


def test_the_command_line_runs_it_and_a_start_on_plaintext_says_what_to_do(plain, capsys):
    with pytest.raises(SystemExit) as ended:
        main(["db", "encrypt", "--dry"])
    assert ended.value.code == encrypt.OK and database.plaintext(plain)
    with pytest.raises(SystemExit) as ended:
        main(["ai", "usage"])
    assert "isn't encrypted yet" in str(ended.value.code) and "db encrypt --dry" in str(ended.value.code)
    with pytest.raises(SystemExit) as ended:
        main(["db", "encrypt"])
    assert ended.value.code == encrypt.OK and not database.plaintext(plain)
