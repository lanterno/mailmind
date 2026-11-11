"""The week's review, written ahead on Monday morning for someone who turned it on: the preference, the background round that
asks for it, and the review Your week then opens on all week, with nothing sent anywhere. When it is written, what it waits
for, how the cost governor holds it back and which stored review a read finds are tests/unit/assistant/test_review_ahead.py's."""
from datetime import datetime, timezone

import pytest

from fakes import anthropic_api as fa
from mailmind.bootstrap import accounts, assistant, background

MONDAY = datetime(2026, 11, 9, 7, 0, tzinfo=timezone.utc)          # the Monday after the app started, in the morning
SUNDAY_NIGHT = datetime(2026, 11, 15, 22, 0, tzinfo=timezone.utc)
WEEK = "A steady week: Bea still wants an answer, and the rest is filed away."


def triaged(ai):
    ai.responder = lambda req: fa.answer({"summary": "Wants an answer.", "importance": 5, "category": "action-needed", "action": "Reply today"})
    assistant.assessments.summarize_pending(limit=100, workers=1)
    ai.responder = None
    ai.requests.clear()


def turned_on(app, on=True):
    assert app.post("/api/preferences", {"weekly_review": on}).status == 200


@pytest.fixture
def ready(app, world, ai):
    """Every message triaged, and Monday morning a week on, when someone turns it on."""
    triaged(ai)
    app.clock.move_to(MONDAY)
    turned_on(app)
    return app


def test_it_is_off_until_someone_turns_it_on(app, world, ai):
    triaged(ai)
    app.clock.move_to(MONDAY)
    assert app.get("/api/preferences").json["preferences"]["weekly_review"] is False
    background.review_ahead_round()
    assert ai.requests == []
    assert app.get("/api/review").json["missing"] is True


def test_turned_on_the_review_is_there_all_week_and_nothing_is_sent(ready, world, ai):
    ai.queue(fa.answer({"narrative": WEEK, "next": []}))
    said_to_smtp = len(world.smtp.commands)
    background.review_ahead_round()
    assert assistant.calls.usage(1)[0]["features"]["review-ahead"]["calls"] == 1
    ready.clock.move_to(SUNDAY_NIGHT)
    r = ready.get("/api/review")
    assert (r.json["narrative"], r.json["cached"]) == (WEEK, True)
    assert len(ai.requests) == 1
    assert (len(world.smtp.commands), world.smtp.messages) == (said_to_smtp, [])
    assert ready.get("/api/outbox").json["items"] == []


def test_turned_off_a_read_finds_only_todays(ready, ai):
    background.review_ahead_round()
    ready.clock.move_to(MONDAY.replace(day=10))
    turned_on(ready, False)
    assert ready.get("/api/review").json["missing"] is True
    assert len(ai.requests) == 1


def test_it_is_a_preference_and_nothing_else_can_be_it(app):
    assert app.post("/api/preferences", {"weekly_review": "yes"}).status == 400
    assert accounts.config.preferences()["weekly_review"] is False
    turned_on(app)
    assert accounts.config.preferences()["weekly_review"] is True
