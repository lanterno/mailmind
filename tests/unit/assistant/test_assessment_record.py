"""What is kept with an assessment, by each path that makes one: the model that served its reply, its prompt's version and
its outcome. And what a row from before those were kept reads as. Everything here is against the fake API."""
import json
import sqlite3

import pytest

from assistant_ports import Model, Triage, calls_on, message, triage_on
from mailmind.assistant.application.ports import ModelError, Reply, Usage
from mailmind.assistant.domain import prompts
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


# ── what triage keeps, on stored mail and a model of its own ──────────────────────────────────────

GOOD = {"summary": "Said once.", "importance": 9, "category": "info", "action": "Reply"}
OTHER = "claude-opus-4-8"       # a model other than the one asked for, as a server-side fallback leaves
LABEL = calls_on(Model()).model_label()
VERSION = prompts.TRIAGE.version


def served(said, stop="end_turn"):
    """A reply `OTHER` served."""
    return Reply(said if isinstance(said, str) else json.dumps(said), OTHER, Usage(), stop)


REFUSED = served("", "refusal")


def triaged(*replies):
    """Triage of one message, with `replies` the model's: what it kept."""
    triage = Triage([message(1)])
    assert triage_on(Model(*replies), triage).summarize_pending(limit=1, workers=1) == len(triage.said)
    return list(triage.said.values())


def test_triage_keeps_the_model_that_served_it_and_the_label_as_it_was():
    assert triaged(served(GOOD)) == [Assessment("Said once.", 5, "info", "Reply", LABEL, OTHER, VERSION, "assessed")]


@pytest.mark.parametrize("reply, outcome", [
    (served("not json at all"), "unusable"),
    (served({"importance": 3}), "unusable"),
    (served({"summary": ""}), "unusable"),
    (REFUSED, "refused"),
], ids=["no json", "no summary", "an empty summary", "a refusal"])
def test_what_stands_in_says_why(reply, outcome):
    [said] = triaged(reply)
    assert (said.model, said.served_by, said.prompt_version, said.outcome) == ("parse-error", OTHER, VERSION, outcome)
    assert (said.importance, said.category, said.action) == (2, "other", "")


def test_a_failed_call_keeps_nothing():
    assert triaged(ModelError("bad request", 400)) == []


# ── what a batch keeps ────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def batch():
    """A batch sent of four messages: the model, the stored mail, the assess job, and the keys its requests went under."""
    model, triage = Model(), Triage(message(n) for n in range(1, 5))
    assessments = triage_on(model, triage)
    assessments.BATCH_MIN, assessments.BATCH_KEEP_NEWEST, assessments.BATCH_POLL = 1, 0, 0
    assert assessments.batch_submit() == 4
    return model, triage, assessments, [int(cid) for cid in model.batched]


def test_a_batch_keeps_what_each_answer_came_of(batch):
    model, triage, assessments, (good, unusable, refused, _) = batch
    model.end_batch({str(good): served(GOOD), str(unusable): served("not json"), str(refused): REFUSED})
    assessments.batch_collect()
    assert triage.said[good] == Assessment("Said once.", 5, "info", "Reply", LABEL, OTHER, VERSION, "assessed")
    assert (triage.said[unusable].model, triage.said[unusable].outcome) == ("parse-error", "unusable")
    assert (triage.said[refused].model, triage.said[refused].served_by, triage.said[refused].outcome) == \
        ("parse-error", OTHER, "refused")


def test_a_batch_remembers_the_version_it_was_sent_with(batch):
    _, triage, _, _ = batch
    assert [b["prompt_version"] for b in triage.batches.values()] == [VERSION]


@pytest.mark.parametrize("sent_with", [VERSION - 1, None], ids=["older words", "from before they were kept"])
def test_answers_that_come_in_after_an_upgrade_say_the_words_they_answered(batch, sent_with):
    model, triage, assessments, keys = batch
    triage.batch_open(None)["prompt_version"] = sent_with
    model.end_batch()
    assessments.batch_collect()
    assert triage.said[keys[0]].prompt_version == sent_with


def gone(triage, key):
    """Every copy of the message under `key` has gone."""
    triage.rows = {pk: r for pk, r in triage.rows.items() if r["message_key"] != key}


def test_an_answer_for_a_message_whose_copies_went_keeps_it_too(batch):
    model, triage, assessments, keys = batch
    gone(triage, keys[0])
    model.end_batch({str(keys[0]): served(GOOD)})
    assessments.batch_collect()
    assert (triage.said[keys[0]].served_by, triage.said[keys[0]].outcome) == (OTHER, "assessed")


def test_a_stand_in_for_a_message_whose_copies_went_is_not_kept(batch):
    model, triage, assessments, keys = batch
    gone(triage, keys[0])
    model.end_batch({str(keys[0]): REFUSED})
    assessments.batch_collect()
    assert keys[0] not in triage.said
