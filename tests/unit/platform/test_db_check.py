"""`python -m mailmind db check-upgrade` (mailmind.platform.db.check) on fictional databases in temp
folders: it reports what the next start's migrations would do, on a copy, in row counts and
verdicts only, and never writes the database it checks."""
import hashlib

import pytest

from mailmind.__main__ import main
from mailmind.platform.db import check, database, migrate, migrations

NEWEST = migrations.MIGRATIONS[-1][0]
MARK = "quilt-pattern-7731"            # in every row written here; no report may show it


def database_at(folder, version):
    """mailmind.db in `folder` at `version`, holding two indexed messages, a stored body and a
    muted thread."""
    path = str(folder / "mailmind.db")
    migrate.init(path, migrations.MIGRATIONS[:version])
    c = database.keyed(path)
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
    index, which every reader of the database writes its read locks to, and an empty log, which a reader opens
    when there is none: SQLite's own, holding nothing of the database."""
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()
            if not p.name.endswith("-shm") and not (p.name.endswith("-wal") and not p.stat().st_size)}


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


def orphan(path, pk):
    """A body whose message is gone, as an older MailMind with foreign keys off left behind."""
    c = database.keyed(path)                               # foreign keys aren't enforced here
    c.execute("INSERT INTO bodies(message_pk, raw) VALUES (?, ?)", (pk, MARK.encode()))
    c.commit()
    c.close()


def test_a_problem_the_upgrade_adds_fails_the_check_without_saying_what_it_holds(tmp_path, monkeypatch):
    path = database_at(tmp_path, NEWEST - 1)
    orphan(path, 99)
    real = migrate.init

    def init_that_orphans_a_body(db, steps=None):
        real(db, steps)
        orphan(db, 98)
    monkeypatch.setattr(migrate, "init", init_that_orphans_a_body)
    code, report = run(path)
    assert code == check.PROBLEM
    assert "foreign_key_check: 2 problem(s), 1 of them new (bodies 1)" in report and MARK not in report


def test_a_problem_already_there_is_named_by_table_and_passes(tmp_path):
    path = database_at(tmp_path, NEWEST - 1)
    orphan(path, 99)
    code, report = run(path)
    assert code == check.OK, report
    assert "foreign_key_check: 1 problem(s), all there before the upgrade (bodies 1)" in report
    assert MARK not in report


def test_without_a_database_there_is_nothing_to_check(tmp_path):
    code, report = run(str(tmp_path / "mailmind.db"))
    assert code == check.CANNOT_RUN and "No database" in report


def test_the_command_line_exits_with_the_verdict(tmp_path, capsys):
    path = database_at(tmp_path, NEWEST)
    with pytest.raises(SystemExit) as done:
        main(["db", "check-upgrade", "--db", path])
    assert done.value.code == check.OK
    assert "every table kept its rows: yes" in capsys.readouterr().out


def test_a_table_a_migration_retires_is_reported_and_is_no_loss(tmp_path):
    path = database_at(tmp_path, 36)
    c = database.keyed(path)
    c.execute("INSERT INTO briefing_cache(key, narrative) VALUES ('ada|2026-11-06|v2', ?)", (MARK,))
    c.commit()
    c.close()
    code, report = run(path)
    assert code == check.OK, report
    [line] = [x for x in report.splitlines() if x.split()[:1] == ["briefing_cache"]]
    assert line.split()[1:] == ["1", "-", "retired"]
    assert "every table kept its rows: yes" in report and MARK not in report


def test_a_table_no_migration_retired_is_a_loss(tmp_path, monkeypatch):
    path = database_at(tmp_path, NEWEST - 1)
    real = migrate.init

    def init_that_drops_a_table(db, steps=None):
        real(db, steps)
        c = database.keyed(db)
        c.execute("DROP TABLE muted")
        c.commit()
        c.close()
    monkeypatch.setattr(migrate, "init", init_that_drops_a_table)
    code, report = run(path)
    assert code == check.PROBLEM
    assert "every table kept its rows: no (muted)" in report and MARK not in report


# -- m28 takes some part rows on purpose, and opening the message records them again ---------------

def holding_an_attached_email(folder, version):
    """A database at `version`: message 1 has an email attached and two part rows, and message 2 a part row and none."""
    path = database_at(folder, version)
    c = database.keyed(path)
    for pk, ctype in ((1, "application/pdf"), (1, "Message/RFC822"), (2, "application/pdf")):
        c.execute("INSERT INTO attachments(message_pk, part_id, filename, content_type) VALUES (?, '2', 'f', ?)", (pk, ctype))
    c.commit()
    c.close()
    return path


def attachments_line(report):
    return next(line.split() for line in report.splitlines() if line.startswith("  attachments"))


def losing_a_part_row(monkeypatch, which="id=(SELECT max(id) FROM attachments)"):
    """A start that also loses rows no migration means to."""
    real = migrate.init

    def init(db, steps=None):
        real(db, steps)
        c = database.keyed(db)
        c.execute(f"DELETE FROM attachments WHERE {which}")
        c.commit()
        c.close()
    monkeypatch.setattr(migrate, "init", init)


def test_the_part_rows_m28_takes_on_purpose_are_no_loss(tmp_path):
    code, report = run(holding_an_attached_email(tmp_path, 27))
    assert attachments_line(report) == ["attachments", "3", "1", "trimmed"]
    assert code == check.OK and "every table kept its rows: yes" in report, report


def test_a_row_more_than_m28_takes_is_a_loss(tmp_path, monkeypatch):
    path = holding_an_attached_email(tmp_path, 27)
    losing_a_part_row(monkeypatch)
    code, report = run(path)
    assert attachments_line(report) == ["attachments", "3", "0", "CHANGED"]
    assert code == check.PROBLEM and "every table kept its rows: no (attachments)" in report, report


def test_a_database_past_m28_has_no_part_rows_to_lose(tmp_path, monkeypatch):
    """Even the very rows m28 would have taken, since it has run."""
    path = holding_an_attached_email(tmp_path, 28)
    assert run(path)[0] == check.OK
    losing_a_part_row(monkeypatch, "message_pk=1")
    code, report = run(path)
    assert attachments_line(report) == ["attachments", "3", "1", "CHANGED"]
    assert code == check.PROBLEM, report


def test_a_database_with_no_attached_email_loses_no_part_rows_in_a_trim(tmp_path, monkeypatch):
    path = database_at(tmp_path, 27)
    c = database.keyed(path)
    c.execute("INSERT INTO attachments(message_pk, part_id, filename, content_type) VALUES (1, '2', 'f', 'application/pdf')")
    c.commit()
    c.close()
    losing_a_part_row(monkeypatch)
    code, report = run(path)
    assert attachments_line(report) == ["attachments", "1", "0", "CHANGED"]
    assert code == check.PROBLEM, report
