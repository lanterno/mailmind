"""What is kept with an assessment, by each path that makes one: the model that served its reply, its prompt's version and
its outcome. And what a row from before those were kept reads as. Everything here is against the fake API."""
import sqlite3

import pytest

from mailmind.bootstrap.assistant import answers
from mailmind.kernel.assessment import Assessment
from mailmind.platform.db.migrations import m0043_assessment_record


# ── a row from before ─────────────────────────────────────────────────────────

@pytest.fixture
def old_database():
    """The two tables as migration 42 left them, an assessment in each state a row could have reached."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE assessments(message_key INTEGER PRIMARY KEY, summary TEXT, importance INTEGER, category TEXT, "
              "action TEXT, model TEXT, created_at TEXT)")
    c.execute("CREATE TABLE ai_batches(id INTEGER PRIMARY KEY AUTOINCREMENT, feature TEXT)")
    c.execute("INSERT INTO assessments VALUES (7, 'Old words', 4, 'info', 'Reply', 'claude-old', '2026-01-01')")
    c.execute("INSERT INTO assessments VALUES (8, 'A stand-in', 2, 'other', '', 'parse-error', '2026-01-01')")
    yield c
    c.close()


def test_an_old_row_keeps_what_it_said_and_reads_as_unknown_for_the_rest(old_database):
    m0043_assessment_record.migrate(old_database)
    assert answers.assessment(old_database, 7) == Assessment("Old words", 4, "info", "Reply", "claude-old", None, None, None)


def test_an_old_stand_in_is_not_taken_for_an_unusable_reply(old_database):
    m0043_assessment_record.migrate(old_database)     # `parse-error` once meant a refusal too: nothing can tell them apart
    assert answers.assessment(old_database, 8).outcome is None


def test_the_migration_can_run_again(old_database):
    m0043_assessment_record.migrate(old_database)
    old_database.execute("UPDATE assessments SET outcome='assessed' WHERE message_key=7")
    m0043_assessment_record.migrate(old_database)
    assert answers.assessment(old_database, 7).outcome == "assessed"
