"""What is kept with an assessment, by each path that makes one: the model that served its reply, its prompt's version and
its outcome. And what a row from before those were kept reads as. Everything here is against the fake API."""
import sqlite3

import pytest

from fakes import anthropic_api as fa
from mailmind.assistant.domain import prompts
from mailmind.bootstrap import assistant
from mailmind.bootstrap.assistant import answers
from mailmind.bootstrap.mailbox import store
from mailmind.kernel.assessment import Assessment
from mailmind.platform.db.migrations import m0043_assessment_record

GOOD = {"summary": "Said once.", "importance": 9, "category": "info", "action": "Reply"}
OTHER = "claude-opus-4-8"       # a model other than the one asked for, as a server-side fallback leaves


def message(obj, model=OTHER, stop="end_turn"):
    _, out = fa.answer(obj, stop)
    return {**out, "model": model}


REFUSED = {"model": OTHER, "stop_reason": "refusal", "content": [], "usage": {}}


def sql(q, *args):
    c = store.conn()
    try:
        return c.execute(q, args).fetchall()
    finally:
        c.close()


def write(q, *args):
    c = store.conn()
    try:
        c.execute(q, args)
        c.commit()
    finally:
        c.close()


def kept():
    """Every assessment there is, as the values they read as."""
    c = store.conn()
    try:
        return [answers.assessment(c, r["message_key"]) for r in c.execute("SELECT message_key FROM assessments")]
    finally:
        c.close()


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


def test_a_message_with_no_assessment_has_none(world):
    c = store.conn()
    try:
        assert answers.assessment(c, 999_999) is None
    finally:
        c.close()


def test_saying_nothing_of_how_leaves_it_unknown(world):
    key = sql("SELECT message_key FROM messages LIMIT 1")[0][0]
    c = store.conn()
    try:
        answers.save_assessment(c, key, "s", 3, "info", "", "m", "now", OTHER, 1, "assessed")
        answers.save_assessment(c, key, "t", 3, "info", "", "m", "now")           # what it says now is someone else's
        assert answers.assessment(c, key) == Assessment("t", 3, "info", "", "m")
    finally:
        c.close()


# ── one message at a time ─────────────────────────────────────────────────────

def test_triage_keeps_the_model_that_served_it_and_the_label_as_it_was(world, ai):
    ai.queue((200, message(GOOD)))
    assert assistant.assessments.summarize_pending(limit=1, workers=1) == 1
    assert kept() == [Assessment("Said once.", 5, "info", "Reply", assistant.calls.model_label(), OTHER,
                                 prompts.TRIAGE.version, "assessed")]


@pytest.mark.parametrize("reply, outcome", [
    (message("not json at all"), "unusable"),
    (message({"importance": 3}), "unusable"),
    (message({"summary": ""}), "unusable"),
    (REFUSED, "refused"),
], ids=["no json", "no summary", "an empty summary", "a refusal"])
def test_what_stands_in_says_why(world, ai, reply, outcome):
    ai.queue((200, reply))
    assert assistant.assessments.summarize_pending(limit=1, workers=1) == 1
    [said] = kept()
    assert (said.model, said.served_by, said.prompt_version, said.outcome) == ("parse-error", OTHER, prompts.TRIAGE.version, outcome)
    assert (said.importance, said.category, said.action) == (2, "other", "")


def test_a_failed_call_keeps_nothing(world, ai):
    ai.queue(fa.error(400, "invalid_request_error", "bad request"))
    assert assistant.assessments.summarize_pending(limit=1, workers=1) == 0
    assert kept() == []


# ── a batch ───────────────────────────────────────────────────────────────────

@pytest.fixture
def batch(world, ai, monkeypatch):
    """A batch sent of all the world's mail: the keys its requests went under."""
    monkeypatch.setattr(assistant.assessments, "BATCH_MIN", 1)
    monkeypatch.setattr(assistant.assessments, "BATCH_KEEP_NEWEST", 0)
    monkeypatch.setattr(assistant.assessments, "BATCH_POLL", 0)
    assert assistant.assessments.batch_submit() > 0
    return [int(r["custom_id"]) for b in ai.batches.values() for r in b["requests"]]


def said_of(key):
    c = store.conn()
    try:
        return answers.assessment(c, key)
    finally:
        c.close()


def test_a_batch_keeps_what_each_answer_came_of(batch, ai):
    good, unusable, refused, *_ = batch
    ai.end_batch(results={str(good): {"type": "succeeded", "message": message(GOOD)},
                          str(unusable): {"type": "succeeded", "message": message("not json")},
                          str(refused): {"type": "succeeded", "message": REFUSED}})
    assistant.assessments.batch_collect()
    version = prompts.TRIAGE.version
    assert said_of(good) == Assessment("Said once.", 5, "info", "Reply", assistant.calls.model_label(), OTHER, version, "assessed")
    assert (said_of(unusable).model, said_of(unusable).outcome) == ("parse-error", "unusable")
    assert (said_of(refused).model, said_of(refused).served_by, said_of(refused).outcome) == ("parse-error", OTHER, "refused")


def test_a_batch_remembers_the_version_it_was_sent_with(batch):
    assert sql("SELECT prompt_version FROM ai_batches")[0][0] == prompts.TRIAGE.version


@pytest.mark.parametrize("sent_with", [prompts.TRIAGE.version - 1, None], ids=["older words", "from before they were kept"])
def test_answers_that_come_in_after_an_upgrade_say_the_words_they_answered(batch, ai, sent_with):
    write("UPDATE ai_batches SET prompt_version=?", sent_with)
    ai.end_batch()
    assistant.assessments.batch_collect()
    assert said_of(batch[0]).prompt_version == sent_with


def test_an_answer_for_a_message_whose_copies_went_keeps_it_too(batch, ai):
    key = batch[0]
    write("DELETE FROM messages WHERE message_key=?", key)
    ai.end_batch(results={str(key): {"type": "succeeded", "message": message(GOOD)}})
    assistant.assessments.batch_collect()
    assert (said_of(key).served_by, said_of(key).outcome) == (OTHER, "assessed")


def test_a_stand_in_for_a_message_whose_copies_went_is_not_kept(batch, ai):
    key = batch[0]
    write("DELETE FROM messages WHERE message_key=?", key)
    ai.end_batch(results={str(key): {"type": "succeeded", "message": REFUSED}})
    assistant.assessments.batch_collect()
    assert said_of(key) is None
