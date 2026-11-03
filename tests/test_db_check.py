"""`python -m mailmind db check-upgrade` (mailmind.platform.db.check) on fictional databases in temp
folders: it reports what the next start's migrations would do, on a copy, in row counts and
verdicts only, and never writes the database it checks."""
import hashlib
import sqlite3

import pytest

from mailmind.__main__ import main
from mailmind.platform.db import check, migrate, migrations

NEWEST = migrations.MIGRATIONS[-1][0]
MARK = "quilt-pattern-7731"            # in every row written here; no report may show it


def database_at(folder, version):
    """mailmind.db in `folder` at `version`, holding two indexed messages, a stored body and a
    muted thread."""
    path = str(folder / "mailmind.db")
    migrate.init(path, migrations.MIGRATIONS[:version])
    c = sqlite3.connect(path)
    for uid in (1, 2):
        c.execute("INSERT INTO messages(account, kind, folder, uid, message_id, subject, from_addr) "
                  "VALUES ('ada@example.test', 'imap', 'INBOX', ?, ?, ?, 'lin@example.test')",
                  (uid, f"<{uid}@example.test>", f"{MARK} {uid}"))
        c.execute("INSERT INTO msg_fts(rowid, subject) VALUES (?, ?)", (uid, f"{MARK} {uid}"))
    c.execute("INSERT INTO bodies(message_pk, raw) VALUES (1, ?)", (MARK.encode(),))
    c.execute("INSERT INTO muted(account, thread_id, muted_at) VALUES ('ada@example.test', ?, 'now')", (MARK,))
    c.commit()
    c.close()
    return path


def run(path):
    lines = []
    code = check.check_upgrade(path, say=lines.append)
    return code, "\n".join(lines)


def fingerprint(folder):
    """Every file in `folder`, by name and content hash, but for the write-ahead log's shared-memory
    index, which every reader of the database writes its read locks to."""
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()
            if not p.name.endswith("-shm")}


@pytest.mark.parametrize("version", [NEWEST - 1, NEWEST])
def test_it_reports_counts_and_verdicts_and_leaves_the_database_alone(tmp_path, version):
    path = database_at(tmp_path, version)
    before = fingerprint(tmp_path)
    code, report = run(path)
    assert code == check.OK, report
    assert f"migrated from version {version} to {NEWEST}" in report
    rows = {line.split()[0]: line.split()[1:] for line in report.splitlines()[3:] if line.startswith("  ")
            and len(line.split()) >= 3 and line.split()[1].isdigit()}
    assert rows["messages"] == ["2", "2"] and rows["bodies"] == ["1", "1"] and rows["muted"] == ["1", "1"]
    for verdict in ("integrity_check: ok", "foreign_key_check: ok", "full-text index: ok",
                    "every table kept its rows: yes"):
        assert verdict in report
    assert MARK not in report
    assert fingerprint(tmp_path) == before                  # nothing written, nothing left beside it


def test_a_problem_fails_the_check_without_saying_what_it_holds(tmp_path):
    path = database_at(tmp_path, NEWEST)
    c = sqlite3.connect(path)                               # foreign keys aren't enforced here
    c.execute("INSERT INTO bodies(message_pk, raw) VALUES (99, ?)", (MARK.encode(),))
    c.commit()
    c.close()
    code, report = run(path)
    assert code == check.PROBLEM
    assert "foreign_key_check: 1 problem(s)" in report and MARK not in report


def test_without_a_database_there_is_nothing_to_check(tmp_path):
    code, report = run(str(tmp_path / "mailmind.db"))
    assert code == check.CANNOT_RUN and "No database" in report


def test_the_command_line_exits_with_the_verdict(tmp_path, capsys):
    path = database_at(tmp_path, NEWEST)
    with pytest.raises(SystemExit) as done:
        main(["db", "check-upgrade", "--db", path])
    assert done.value.code == check.OK
    assert "every table kept its rows: yes" in capsys.readouterr().out
