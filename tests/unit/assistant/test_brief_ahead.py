"""The morning's brief, written ahead of the first open: for someone who turned it on, in the morning, once triage has read
the last day's mail, and within the cost governor."""
from datetime import datetime, timedelta, timezone

import pytest

from fakes import anthropic_api as fa
from mailmind.bootstrap import accounts, assistant, background
from mailmind.bootstrap.mailbox import store

MORNING = datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc)
CLIENT = "bea-inbox-01-client.eml"


def triaged(ai):
    ai.responder = lambda req: fa.answer({"summary": "Wants an answer.", "importance": 5, "category": "action-needed", "action": "Reply today"})
    assistant.assessments.summarize_pending(limit=100, workers=1)
    ai.responder = None
    ai.requests.clear()


def turned_on(app):
    assert app.post("/api/preferences", {"morning_brief": True}).status == 200


def not_triaged(world, name, age):
    """Leave one message unread by triage, `age` before the morning."""
    c = store.conn()
    key = world.key(name)
    c.execute("DELETE FROM assessments WHERE message_key=?", (key,))
    c.execute("UPDATE messages SET ts=? WHERE message_key=?", (int((MORNING - age).timestamp()), key))
    c.commit()
    c.close()


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


def test_a_brief_already_written_today_is_left_alone(ready, ai):
    background.brief_ahead_round()
    background.brief_ahead_round()
    assert len(written(ai)) == 1


@pytest.mark.parametrize("hour", [0, 4, 12, 23])
def test_outside_the_morning_nothing_is_written(ready, ai, hour):
    ready.clock.move_to(MORNING.replace(hour=hour))
    background.brief_ahead_round()
    assert ai.requests == []


@pytest.mark.parametrize("hour", [5, 11])
def test_the_morning_is_from_five_until_noon(ready, ai, hour):
    ready.clock.move_to(MORNING.replace(hour=hour))
    background.brief_ahead_round()
    assert len(written(ai)) == 1


def test_it_waits_for_triage_to_read_the_last_days_mail(ready, world, ai):
    not_triaged(world, CLIENT, timedelta(hours=2))
    background.brief_ahead_round()
    assert ai.requests == []


def test_an_older_backlog_doesnt_hold_it_up(ready, world, ai):
    not_triaged(world, CLIENT, timedelta(days=3))
    background.brief_ahead_round()
    assert len(written(ai)) == 1


def test_after_days_away_nothing_is_written_until_someone_is_back(ready, ai):
    ready.clock.move_to(MORNING + timedelta(days=2))
    background.brief_ahead_round()
    assert ai.requests == []
    assistant.governor().saw_someone()
    background.brief_ahead_round()
    assert len(written(ai)) == 1


def test_a_day_off_leaves_it_on(ready, ai):
    ready.clock.move_to(MORNING + timedelta(days=1))       # nobody has been here since the day before yesterday's noon
    background.brief_ahead_round()
    assert len(written(ai)) == 1


def test_a_failed_call_waits_for_the_next_round(ready, ai):
    ai.queue(fa.error(400, "invalid_request_error", "bad request"))
    background.brief_ahead_round()
    assert ready.get("/api/briefing").json["missing"] is True
    background.brief_ahead_round()
    assert ready.get("/api/briefing").json["cached"] is True


def test_it_is_a_preference_and_nothing_else_can_be_it(app):
    assert app.post("/api/preferences", {"morning_brief": "yes"}).status == 400
    assert accounts.config.preferences()["morning_brief"] is False
    turned_on(app)
    assert accounts.config.preferences()["morning_brief"] is True
