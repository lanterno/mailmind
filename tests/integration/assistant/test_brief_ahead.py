"""The morning's brief, written ahead of the first open, for someone who turned it on: the preference, the background round
that asks for it, and the brief the Inbox then opens on. When in the morning it is written, what it waits for and how the
cost governor holds it back are tests/unit/assistant/test_brief_ahead.py's."""
from datetime import datetime, timezone

import pytest

from fakes import anthropic_api as fa
from mailmind.bootstrap import accounts, assistant, background

MORNING = datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc)


def triaged(ai):
    ai.responder = lambda req: fa.answer({"summary": "Wants an answer.", "importance": 5, "category": "action-needed", "action": "Reply today"})
    assistant.assessments.summarize_pending(limit=100, workers=1)
    ai.responder = None
    ai.requests.clear()


def turned_on(app):
    assert app.post("/api/preferences", {"morning_brief": True}).status == 200


@pytest.fixture
def ready(app, world, ai):
    """Every message triaged, the preference on, and early the next morning."""
    triaged(ai)
    turned_on(app)
    app.clock.move_to(MORNING)
    return app


def written(ai):
    return [r for r in ai.requests if "narrative" in str(r.schema)]


def test_it_is_off_until_someone_turns_it_on(app, world, ai):
    triaged(ai)
    app.clock.move_to(MORNING)
    assert app.get("/api/preferences").json["preferences"]["morning_brief"] is False
    background.brief_ahead_round()
    assert ai.requests == []
    assert app.get("/api/briefing").json["missing"] is True


def test_turned_on_the_brief_is_there_when_the_inbox_opens(ready, ai):
    ai.queue(fa.answer({"narrative": "Bea wants an answer.", "groups": []}))
    background.brief_ahead_round()
    assert assistant.calls.usage(1)[0]["features"]["brief-ahead"]["calls"] == 1
    r = ready.get("/api/briefing")
    assert (r.json["narrative"], r.json["cached"]) == ("Bea wants an answer.", True)
    assert len(written(ai)) == 1


def test_it_is_a_preference_and_nothing_else_can_be_it(app):
    assert app.post("/api/preferences", {"morning_brief": "yes"}).status == 400
    assert accounts.config.preferences()["morning_brief"] is False
    turned_on(app)
    assert accounts.config.preferences()["morning_brief"] is True
