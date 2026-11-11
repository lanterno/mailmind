"""Migration 45: what was written only for a rollback goes, and what a run of an older commit wrote only there comes across
first, of a row with or without a key. Each database here is fictional, built at its version in a temp folder."""
import contextlib

import pytest

from mailmind.bootstrap.assistant import answers
from mailmind.bootstrap.mailbox import store
from mailmind.platform import settings
from mailmind.platform.db import check, database, migrate, migrations
from mailmind.platform.db.migrations import m0045_rollback_columns as m45

ADA = "ada@example.test"
UNTIL = 4102444800
MARK = "quilt-pattern-7731"             # in what a report might show; none may
ENDED = (("messages", "snoozed_until"), ("outbox", "in_reply_to_pk"), ("drafts", "in_reply_to_pk"), ("draft_ahead", "src_pk"),
         ("followups", "anchor_pk"))


def at(tmp_path, version=44):
    path = str(tmp_path / "mailmind.db")
    migrate.init(path, migrations.MIGRATIONS[:version])
    return path


@contextlib.contextmanager
def writing(path):
    c = database.keyed(path)           # foreign keys aren't enforced here, as an older MailMind left them
    try:
        yield c
        c.commit()
    finally:
        c.close()


def rows(path, sql, *args):
    c = database.keyed(path)
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def mail(c, uid=1, folder="INBOX", message_id="<a@example.test>", **more):
    row = {"account": ADA, "kind": "imap", "folder": folder, "uid": uid, "message_id": message_id,
           "from_addr": "lin@example.test", "subject": MARK, "date_iso": "2026-11-02", **more}
    return c.execute(f"INSERT INTO messages({', '.join(row)}) VALUES({', '.join('?' * len(row))})", list(row.values())).lastrowid


def minted(c, identity="<a@example.test>", gone_at=None):
    return c.execute("INSERT INTO message_identity(account, identity, gone_at) VALUES (?, ?, ?)", (ADA, identity, gone_at)).lastrowid


def said(c, pk, summary=MARK, created_at="2026-11-01"):
    c.execute("INSERT INTO summaries(message_pk, summary, importance, category, action, model, created_at) "
              "VALUES (?, ?, 3, 'info', 'Reply', 'claude-old', ?)", (pk, summary, created_at))


def keys(path):
    return dict(rows(path, "SELECT pk, message_key FROM messages"))


def assessment(path, key):
    return rows(path, "SELECT summary, importance, category, action, model, created_at FROM assessments WHERE message_key=?", key)


def snoozes(path):
    return rows(path, "SELECT message_key, folder, until FROM snoozes ORDER BY message_key, folder")


def still_there(path):
    """What migration 45 ends and `path` still holds."""
    left = [t for (t,) in rows(path, "SELECT name FROM sqlite_master WHERE name IN ('summaries', 'idx_msg_snooze') ORDER BY name")]
    return left + [c for c in ENDED if c[1] in [r[1] for r in rows(path, f"PRAGMA table_info({c[0]})")]]


def schema(path):
    return rows(path, "SELECT type, name, sql FROM sqlite_master ORDER BY type, name")


# -- what comes across -------------------------------------------------------------------------------------

def test_a_row_with_a_key_takes_its_summary_and_snooze_across(tmp_path):
    path = at(tmp_path)
    with writing(path) as c:
        key = minted(c)
        pk = mail(c, message_key=key, snoozed_until=UNTIL)
        said(c, pk)
    migrate.init(path)
    assert assessment(path, key) == [(MARK, 3, "info", "Reply", "claude-old", "2026-11-01")]
    assert snoozes(path) == [(key, "INBOX", UNTIL)]


def test_a_row_with_no_key_gets_one_and_takes_them_across_under_it(tmp_path):
    path = at(tmp_path)
    with writing(path) as c:
        pk = mail(c, snoozed_until=UNTIL)
        said(c, pk)
    migrate.init(path)
    key = keys(path)[pk]
    assert key is not None
    assert assessment(path, key)[0][0] == MARK
    assert snoozes(path) == [(key, "INBOX", UNTIL)]


def test_the_key_it_gets_is_the_one_the_app_would_mint(tmp_path):
    """Or the next copy to arrive would be another message, and leave what came across behind."""
    path = at(tmp_path)
    with writing(path) as c:
        by_id = mail(c, uid=1, message_id=" <a@\r\n example.test> ")
        by_envelope = mail(c, uid=2, message_id=None)
    migrate.init(path)
    c = database.keyed(path)
    try:
        assert store.mint_key(c, ADA, "<a@example.test>", "lin@example.test", "2026-11-02", MARK) == keys(path)[by_id]
        assert store.mint_key(c, ADA, None, "lin@example.test", "2026-11-02", MARK) == keys(path)[by_envelope]
        assert c.execute("SELECT COUNT(*) FROM message_identity").fetchone()[0] == 2
    finally:
        c.close()


def test_copies_with_no_key_share_one(tmp_path):
    path = at(tmp_path)
    with writing(path) as c:
        one, other = mail(c, uid=1), mail(c, uid=7, folder="Archive")
        said(c, other)
    migrate.init(path)
    assert keys(path)[one] == keys(path)[other] is not None
    assert assessment(path, keys(path)[one])[0][0] == MARK


def test_a_key_a_row_comes_back_to_is_no_longer_gone(tmp_path):
    path = at(tmp_path)
    with writing(path) as c:
        key = minted(c, gone_at=1)
        pk = mail(c)
    migrate.init(path)
    assert keys(path)[pk] == key
    assert rows(path, "SELECT gone_at FROM message_identity WHERE key=?", key) == [(None,)]


def test_what_a_key_already_holds_stands(tmp_path):
    path = at(tmp_path)
    with writing(path) as c:
        key = minted(c)
        pk = mail(c, message_key=key, snoozed_until=UNTIL)
        said(c, pk, "Said by the old one")
        c.execute("INSERT INTO assessments(message_key, summary, importance) VALUES (?, 'Said under the key', 4)", (key,))
        c.execute("INSERT INTO snoozes(message_key, folder, until) VALUES (?, 'INBOX', 1000)", (key,))
    migrate.init(path)
    assert assessment(path, key)[0][0] == "Said under the key"
    assert snoozes(path) == [(key, "INBOX", 1000)]


def test_of_two_copies_the_newer_summary_comes_across(tmp_path):
    path = at(tmp_path)
    with writing(path) as c:
        key = minted(c)
        said(c, mail(c, uid=1, message_key=key), "Older", "2026-10-01")
        said(c, mail(c, uid=2, folder="Archive", message_key=key), "Newer", "2026-11-01")
    migrate.init(path)
    assert assessment(path, key)[0][0] == "Newer"


def test_a_snooze_comes_across_for_each_folder_it_was_set_in(tmp_path):
    path = at(tmp_path)
    with writing(path) as c:
        key = minted(c)
        mail(c, uid=1, message_key=key, snoozed_until=UNTIL)
        mail(c, uid=2, folder="Archive", message_key=key, snoozed_until=UNTIL + 60)
    migrate.init(path)
    assert snoozes(path) == [(key, "Archive", UNTIL + 60), (key, "INBOX", UNTIL)]


def test_a_row_that_is_not_snoozed_stays_so(tmp_path):
    path = at(tmp_path)
    with writing(path) as c:
        mail(c)
    migrate.init(path)
    assert snoozes(path) == []


def test_a_summary_whose_message_has_gone_goes_with_the_table(tmp_path):
    path = at(tmp_path)
    with writing(path) as c:
        said(c, 99)
    migrate.init(path)
    assert rows(path, "SELECT COUNT(*) FROM assessments") == [(0,)] and "summaries" not in still_there(path)


@pytest.mark.parametrize("version", [0, 7, 19, 20, 29, 30, 32, 33, 44])
def test_from_any_version_what_they_held_comes_across_and_they_go(tmp_path, version):
    path = at(tmp_path, version)
    with writing(path) as c:
        pk = mail(c, snoozed_until=UNTIL)
        said(c, pk)
    migrate.init(path)
    key = keys(path)[pk]
    assert assessment(path, key)[0][0] == MARK
    assert snoozes(path) == [(key, "INBOX", UNTIL)]
    assert still_there(path) == []
    assert rows(path, "PRAGMA user_version") == [(migrations.MIGRATIONS[-1][0],)]
    assert rows(path, "PRAGMA integrity_check") == [("ok",)] and rows(path, "PRAGMA foreign_key_check") == []


def test_the_app_reads_what_came_across(tmp_path):
    with settings.use(settings.Settings(home=str(tmp_path))):
        path = at(tmp_path)
        with writing(path) as c:
            pk = mail(c, snoozed_until=UNTIL)
            said(c, pk, "Lin asks about lunch.")
            other = mail(c, uid=2, message_id="<b@example.test>")
        store.init()
        c = store.conn()
        try:
            row = store.get_message(c, pk)
            assert row["snooze_until"] == UNTIL
            assert answers.assessment(c, row["message_key"]).summary == "Lin asks about lunch."
            assert {r["pk"] for r in store.folder_rows(c, ADA, "INBOX", 50, 0)} == {other}
            assert [r["pk"] for r in store.snoozed_rows(c, ADA, 50, 0)] == [pk]
        finally:
            c.close()


# -- what goes -----------------------------------------------------------------------------------------------

def test_each_is_there_before_and_gone_after(tmp_path):
    path = at(tmp_path)
    assert still_there(path) == ["idx_msg_snooze", "summaries", *ENDED]
    migrate.init(path)
    assert still_there(path) == []


def test_a_new_database_never_shows_them(tmp_path):
    path = str(tmp_path / "new.db")
    migrate.init(path)
    assert still_there(path) == []


def test_what_else_a_row_held_stays(tmp_path):
    path = at(tmp_path)
    with writing(path) as c:
        pk = mail(c, snippet="The marigold border")
        c.execute("INSERT INTO bodies(message_pk, raw) VALUES (?, 'raw')", (pk,))
        c.execute("""INSERT INTO outbox(account, idempotency_key, message_id, in_reply_to_pk, envelope, recipients, raw, created_at)
                     VALUES (?, 'k', '<o@example.test>', ?, '[]', '{}', X'00', 'then')""", (ADA, pk))
        c.execute("INSERT INTO drafts(account, in_reply_to_pk, subject) VALUES (?, ?, 'Re: Lunch')", (ADA, pk))
        c.execute("INSERT INTO draft_ahead(account, ref_key, src_pk, src_message_id) VALUES (?, 't:T', ?, '<a@example.test>')", (ADA, pk))
        c.execute("INSERT INTO followups(account, thread_id, kind, anchor_pk) VALUES (?, 'T', 'sent', ?)", (ADA, pk))
    migrate.init(path)
    assert rows(path, "SELECT pk, snippet FROM messages") == [(pk, "The marigold border")]
    assert rows(path, "SELECT message_pk FROM bodies") == [(pk,)]
    assert rows(path, "SELECT idempotency_key FROM outbox") == [("k",)]
    assert rows(path, "SELECT subject FROM drafts") == [("Re: Lunch",)]
    assert rows(path, "SELECT src_message_id FROM draft_ahead") == [("<a@example.test>",)]
    assert rows(path, "SELECT thread_id, kind FROM followups") == [("T", "sent")]


# -- safe to run again -----------------------------------------------------------------------------------------

def test_it_can_run_again(tmp_path):
    path = at(tmp_path)
    with writing(path) as c:
        pk = mail(c, snoozed_until=UNTIL)
        said(c, pk)
    migrate.init(path)
    before = schema(path), assessment(path, keys(path)[pk]), snoozes(path), keys(path)
    with writing(path) as c:
        m45.migrate(c)
    assert (schema(path), assessment(path, keys(path)[pk]), snoozes(path), keys(path)) == before


@pytest.mark.parametrize("already", ["DROP TABLE summaries",
                                     "DROP INDEX idx_msg_snooze",
                                     "ALTER TABLE outbox DROP COLUMN in_reply_to_pk",
                                     "DROP TABLE followups"])
def test_it_goes_by_what_is_there(tmp_path, already):
    path = at(tmp_path)
    with writing(path) as c:
        c.execute(already)
        m45.migrate(c)
    assert still_there(path) == []


def test_a_failure_leaves_it_all_as_it_was_and_the_next_start_runs_it_again(tmp_path):
    path = at(tmp_path)
    with writing(path) as c:
        pk = mail(c, snoozed_until=UNTIL)
        said(c, pk)

    def fails_at_the_end(c):
        m45.migrate(c)
        raise RuntimeError("the disk filled up")
    before = schema(path)
    with pytest.raises(RuntimeError, match="the disk filled up"):
        migrate.init(path, [*migrations.MIGRATIONS[:44], (45, fails_at_the_end)])
    assert schema(path) == before
    assert rows(path, "SELECT snoozed_until, message_key FROM messages") == [(UNTIL, None)]
    assert rows(path, "SELECT COUNT(*) FROM summaries") == [(1,)]
    assert rows(path, "PRAGMA user_version") == [(44,)]
    migrate.init(path)
    assert assessment(path, keys(path)[pk])[0][0] == MARK and still_there(path) == []


# -- nothing brings them back ----------------------------------------------------------------------------------

@pytest.mark.parametrize("version", [0, 19, 20, 44, 45])
def test_a_later_start_brings_nothing_back(tmp_path, version):
    """The base schema and LEGACY_COLS hold what this ended, so neither may run again."""
    with settings.use(settings.Settings(home=str(tmp_path))):
        path = at(tmp_path, version)
        store.init()
        assert still_there(path) == []
        before = schema(path)
        store.init()
        store.init()
        assert still_there(path) == [] and schema(path) == before


def test_a_start_finds_nothing_left_for_repair_keys_to_read(tmp_path):
    with settings.use(settings.Settings(home=str(tmp_path))):
        store.init()
        with writing(settings.current().db) as c:
            pk = mail(c)
        store.init()
        assert keys(settings.current().db)[pk] is not None


# -- the owner's dry run ---------------------------------------------------------------------------------------

def report_on(path):
    lines = []
    return check.check_upgrade(path, say=lines.append), "\n".join(lines)


def line_for(report, table):
    [line] = [x for x in report.splitlines() if x.split()[:1] == [table]]
    return line.split()[1:]


def holding_what_goes(tmp_path):
    path = at(tmp_path)
    with writing(path) as c:
        key = minted(c)
        pk = mail(c, message_key=key, snoozed_until=UNTIL)
        c.execute("INSERT INTO msg_fts(rowid, subject) VALUES (?, ?)", (pk, MARK))
        said(c, pk)
        keyless = mail(c, uid=2, message_id="<b@example.test>", snoozed_until=UNTIL)
        c.execute("INSERT INTO msg_fts(rowid, subject) VALUES (?, ?)", (keyless, MARK))
        said(c, keyless)
        c.execute("""INSERT INTO outbox(account, idempotency_key, message_id, in_reply_to_pk, envelope, recipients, raw, created_at)
                     VALUES (?, 'k', '<o@example.test>', ?, '[]', '{}', X'00', 'then')""", (ADA, pk))
        c.execute("INSERT INTO drafts(account, in_reply_to_pk, subject) VALUES (?, ?, ?)", (ADA, pk, MARK))
        c.execute("INSERT INTO draft_ahead(account, ref_key, src_pk) VALUES (?, 't:T', ?)", (ADA, pk))
        c.execute("INSERT INTO followups(account, thread_id, kind, anchor_pk) VALUES (?, 'T', 'sent', ?)", (ADA, pk))
    return path


def test_the_dry_run_calls_summaries_retired_and_passes(tmp_path):
    code, report = report_on(holding_what_goes(tmp_path))
    assert code == check.OK, report
    assert line_for(report, "summaries") == ["2", "-", "retired"]
    assert "every table kept its rows: yes" in report and MARK not in report


def test_the_dry_run_says_what_came_across_and_it_is_no_loss(tmp_path):
    code, report = report_on(holding_what_goes(tmp_path))
    assert code == check.OK, report
    assert line_for(report, "assessments") == ["0", "2", "carried", "over"]
    assert line_for(report, "snoozes") == ["0", "2", "carried", "over"]
    assert line_for(report, "message_identity") == ["1", "2", "carried", "over"]
    for table, count in (("messages", "2"), ("outbox", "1"), ("drafts", "1"), ("draft_ahead", "1"), ("followups", "1")):
        assert line_for(report, table) == [count, count]


def test_the_dry_run_leaves_the_database_as_it_was(tmp_path):
    path = holding_what_goes(tmp_path)
    before = schema(path)
    report_on(path)
    assert schema(path) == before and rows(path, "PRAGMA user_version") == [(44,)]


def test_a_table_a_carry_over_shrinks_is_still_a_loss(tmp_path, monkeypatch):
    path = holding_what_goes(tmp_path)
    with writing(path) as c:
        key = rows(path, "SELECT key FROM message_identity")[0][0]
        c.execute("INSERT INTO assessments(message_key, summary) VALUES (?, 'x')", (key,))
    real = migrate.init

    def init_that_loses_one(db, steps=None):
        real(db, steps)
        with writing(db) as c:
            c.execute("DELETE FROM assessments")
    monkeypatch.setattr(migrate, "init", init_that_loses_one)
    code, report = report_on(path)
    assert code == check.PROBLEM and "every table kept its rows: no (assessments)" in report


def test_growth_is_no_carry_over_once_the_migration_that_carries_has_run(tmp_path, monkeypatch):
    path = str(tmp_path / "mailmind.db")
    migrate.init(path)
    real = migrate.init

    def init_that_adds_one(db, steps=None):
        real(db, steps)
        with writing(db) as c:
            c.execute("INSERT INTO message_identity(account, identity) VALUES (?, 'x')", (ADA,))
    monkeypatch.setattr(migrate, "init", init_that_adds_one)
    code, report = report_on(path)
    assert code == check.PROBLEM and "every table kept its rows: no (message_identity)" in report
