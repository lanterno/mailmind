"""What is kept with an assessment, by each path that makes one: the model that served its reply, its prompt's version and
its outcome. And what a row from before those were kept reads as. Everything here is against the fake API."""
from fakes import anthropic_api as fa
from mailmind.assistant.domain import prompts
from mailmind.bootstrap import assistant
from mailmind.bootstrap.assistant import answers
from mailmind.bootstrap.mailbox import store
from mailmind.kernel.assessment import Assessment

OTHER = "claude-opus-4-8"       # a model other than the one asked for, as a server-side fallback leaves


def sql(q, *args):
    c = store.conn()
    try:
        return c.execute(q, args).fetchall()
    finally:
        c.close()


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


# What the store keeps of each, as triage hands it on. What triage hands on is in tests/unit/assistant/test_assessment_record.py.

def kept():
    """Every assessment there is, as the values they read as."""
    c = store.conn()
    try:
        return [answers.assessment(c, r["message_key"]) for r in c.execute("SELECT message_key FROM assessments")]
    finally:
        c.close()


def test_triage_keeps_the_model_that_served_it_and_the_label_as_it_was(world, ai):
    _, out = fa.answer({"summary": "Said once.", "importance": 9, "category": "info", "action": "Reply"})
    ai.queue((200, {**out, "model": OTHER}))
    assert assistant.assessments.summarize_pending(limit=1, workers=1) == 1
    assert kept() == [Assessment("Said once.", 5, "info", "Reply", assistant.calls.model_label(), OTHER,
                                 prompts.TRIAGE.version, "assessed")]


def test_a_batch_remembers_the_version_it_was_sent_with(world, ai, monkeypatch):
    monkeypatch.setattr(assistant.assessments, "BATCH_MIN", 1)
    monkeypatch.setattr(assistant.assessments, "BATCH_KEEP_NEWEST", 0)
    assert assistant.assessments.batch_submit() > 0
    assert sql("SELECT prompt_version FROM ai_batches")[0][0] == prompts.TRIAGE.version
