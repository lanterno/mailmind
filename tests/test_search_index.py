"""The search index holds no copy of the mail (migration 38) and still does all it did."""
import sqlite3

import pytest

from mailmind.platform.db import check, migrate, migrations
from mailmind.platform.db.migrations import m0038_contentless_search as m38
from mailmind.bootstrap import discovery

NEWEST = migrations.MIGRATIONS[-1][0]
ACCOUNT = "ada@example.test"


def hits(c, match):
    return [r[0] for r in c.execute("SELECT rowid FROM msg_fts WHERE msg_fts MATCH ? ORDER BY rowid", (match,))]


def add_message(c, uid, subject, body, sender="lin@example.test"):
    return c.execute("INSERT INTO messages(account, kind, folder, uid, message_id, subject, from_addr, body_text) "
                     "VALUES (?, 'imap', 'INBOX', ?, ?, ?, ?, ?)",
                     (ACCOUNT, uid, f"<{uid}@example.test>", subject, sender, body)).lastrowid


@pytest.fixture
def before(tmp_path):
    """A database a version short of the migration, with mail, opened at that version."""
    path = str(tmp_path / "mailmind.db")
    migrate.init(path, migrations.MIGRATIONS[:37])
    return path


def test_the_index_keeps_no_copy_of_the_mail(data_dir):
    import store
    c = store.conn()
    pk = add_message(c, 1, "Marigold border", "Four more squares")
    discovery.index_message(c, pk)
    c.commit()
    assert c.execute("SELECT subject, body_text FROM msg_fts WHERE rowid=?", (pk,)).fetchone()[:] == (None, None)
    assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='msg_fts_content'").fetchone()
    assert hits(c, '"marigold"') == [pk] and hits(c, 'body_text : "squares"') == [pk]
    c.close()


def test_the_migration_builds_the_index_from_what_the_messages_hold(before):
    c = sqlite3.connect(before)
    kept = add_message(c, 1, "Marigold border", "Four more squares")
    c.execute("INSERT INTO msg_fts(rowid, subject, body_text) VALUES (?, 'stale words', 'stale words')", (kept,))
    missed = add_message(c, 2, "Allotment rota", "Your turn on Saturday")
    c.execute("INSERT INTO msg_fts(rowid, subject) VALUES (999, 'an orphan')")
    nulls = add_message(c, 3, None, None)
    c.commit()
    c.close()
    migrate.init(before)
    c = sqlite3.connect(before)
    assert hits(c, '"marigold"') == [kept] and hits(c, '"saturday"') == [missed]
    assert hits(c, '"stale"') == [] and hits(c, '"orphan"') == []
    assert c.execute("SELECT count(*) FROM msg_fts").fetchone()[0] == 3
    assert nulls in [r[0] for r in c.execute("SELECT rowid FROM msg_fts")]
    c.close()


def test_the_migration_again_changes_nothing(before):
    c = sqlite3.connect(before)
    add_message(c, 1, "Marigold border", "Four more squares")
    c.commit()
    c.close()
    migrate.init(before)
    c = sqlite3.connect(before)
    pk = add_message(c, 2, "Allotment rota", "Saturday")
    c.execute("INSERT INTO msg_fts(rowid, subject) VALUES (?, 'rota')", (pk,))
    m38.migrate(c)                 # were it to build again, the index would lose the word only it holds
    assert hits(c, '"rota"') == [pk]
    c.close()


def test_indexing_a_row_again_replaces_its_words(data_dir):
    import store
    c = store.conn()
    pk = add_message(c, 1, "Marigold border", None)
    discovery.index_message(c, pk)
    store.fts_set_body(c, pk, "Four more squares")
    store.fts_set_body(c, pk, "Three more hexagons")
    assert hits(c, '"marigold"') == [pk] and hits(c, 'body_text : "hexagons"') == [pk]
    assert hits(c, 'body_text : "squares"') == []
    c.execute("INSERT INTO msg_fts(msg_fts) VALUES('integrity-check')")
    c.close()


def test_a_row_deleted_from_the_index_is_gone_and_one_it_never_held_is_no_harm(data_dir):
    import store
    c = store.conn()
    pk = add_message(c, 1, "Marigold border", "Four more squares")
    discovery.index_message(c, pk)
    discovery.forget_message(c, pk)
    discovery.forget_message(c, pk + 41)
    assert hits(c, '"marigold"') == []
    assert c.execute("SELECT count(*) FROM msg_fts").fetchone()[0] == 0
    c.execute("INSERT INTO msg_fts(msg_fts) VALUES('integrity-check')")
    c.close()


def test_ranking_still_counts_the_columns(data_dir):
    from mailmind.bootstrap import discovery
    import store
    c = store.conn()
    in_body = add_message(c, 1, "Allotment rota", "the marigold border")
    in_subject = add_message(c, 2, "Marigold border", "Four more squares")
    discovery.index_message(c, in_body)
    discovery.index_message(c, in_subject)
    got = c.execute(f"SELECT rowid FROM msg_fts WHERE msg_fts MATCH ? ORDER BY {discovery.BM25}", ('"marigold"',)).fetchall()
    assert [r[0] for r in got] == [in_subject, in_body]
    c.close()


def test_a_start_with_too_old_a_sqlite_says_so_and_changes_nothing(before, monkeypatch):
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 42, 0))
    monkeypatch.setattr(sqlite3, "sqlite_version", "3.42.0")
    with pytest.raises(RuntimeError, match=r"needs SQLite 3\.43 or newer, and this Python has 3\.42\.0"):
        migrate.init(before)
    monkeypatch.undo()
    c = sqlite3.connect(before)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 37
    c.close()


def report_of(path):
    lines = []
    return check.check_upgrade(path, say=lines.append), "\n".join(lines)


def test_the_upgrade_check_says_the_index_was_rebuilt_and_is_no_loss(before):
    c = sqlite3.connect(before)
    pk = add_message(c, 1, "Marigold border", "Four more squares")
    c.execute("INSERT INTO msg_fts(rowid, subject) VALUES (?, 'marigold')", (pk,))
    c.execute("INSERT INTO msg_fts(rowid, subject) VALUES (999, 'an orphan')")
    c.commit()
    c.close()
    code, report = report_of(before)
    assert code == check.OK, report
    [line] = [x for x in report.splitlines() if x.split()[:1] == ["msg_fts"]]
    assert line.split()[1:] == ["2", "1", "rebuilt"]
    assert "every table kept its rows: yes" in report and "full-text index: ok" in report
    assert "msg_fts_" not in report and "Marigold" not in report


def test_the_upgrade_check_fails_on_a_message_the_index_doesnt_hold(tmp_path):
    path = str(tmp_path / "mailmind.db")
    migrate.init(path)
    c = sqlite3.connect(path)
    add_message(c, 1, "Marigold border", "Four more squares")
    c.commit()
    c.close()
    code, report = report_of(path)
    assert code == check.PROBLEM and "full-text index: 1 message(s) not in it" in report
