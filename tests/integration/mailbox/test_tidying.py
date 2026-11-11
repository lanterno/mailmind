"""Tidy on its own: once a day, for someone who turned it on, only what the brief's Tidy offers, and all of it can be taken back.
Here the job through the app, on the AI's verdicts, with Undo; when a run files (once a day, from the hour the day starts) is the
unit half's (tests/unit/mailbox/test_tidying.py)."""
from datetime import datetime, timedelta, timezone

import pytest

from fakes import anthropic_api as fa
from mailmind.bootstrap import assistant, background
from mailmind.bootstrap.assistant import answers
from mailmind.bootstrap.mailbox import store

MORNING = datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc)
SAFE, TOO, ASKS, IN_ARCHIVE = ("ada-inbox-01-lunch.eml", "ada-inbox-02-lunch-confirm.eml", "ada-inbox-04-ci.eml", "ada-archive-01-old-thread.eml")


def judged(world, name, importance=1, category="info", action=""):
    """Say what the AI said of this message."""
    c = store.conn()
    answers.save_summary(c, world.pk(name), "Said.", importance, category, action, "m", "2026-11-02T00:00:00+00:00")
    c.commit()
    c.close()


@pytest.fixture
def on(app, world, ai):
    """The preference on, and early the next morning, with no verdict on anything until a test gives one."""
    assert app.post("/api/preferences", {"auto_tidy": True}).status == 200
    app.clock.move_to(MORNING)
    return app


def round_(app):
    background.tidy_round()
    app.settle()


def in_inbox(world, name):
    return any(box == "INBOX" for _, box, *_ in world.where(name)["server"])


def test_it_is_off_until_someone_turns_it_on(app, world, ai):
    judged(world, SAFE)
    app.clock.move_to(MORNING)
    round_(app)
    assert in_inbox(world, SAFE)
    assert app.get("/api/tidy").json == {"ok": True, "group": None, "filed": 0, "items": []}


def test_what_the_brief_would_tidy_is_filed_and_the_brief_says_so(on, world):
    judged(world, SAFE)
    judged(world, TOO)
    round_(on)
    assert not in_inbox(world, SAFE) and not in_inbox(world, TOO)
    got = on.get("/api/tidy").json
    assert (got["group"], got["filed"]) == ("tidy-2026-11-03", 2)
    assert {"subject": "Lunch on Thursday?", "who": "Lin Mori"} in got["items"]


def test_a_persons_own_filing_is_no_run_of_tidys(on, world):
    assert on.post("/api/bulk/file", {"verb": "archive", "rows": [world.copy(SAFE)], "undo_group": "bulk:mine"}).status == 200
    on.settle()
    assert on.get("/api/tidy").json["group"] is None


@pytest.mark.parametrize("verdict", [
    {"importance": 3},                                      # not low
    {"category": "newsletter"},                             # lives in the Feed
    {"category": "action-needed"},                          # asked of you
    {"action": "Reply by Friday"},                          # a next step
])
def test_what_the_brief_wouldnt_tidy_stays(on, world, verdict):
    judged(world, TOO, **verdict)
    round_(on)
    assert in_inbox(world, TOO)
    assert on.get("/api/tidy").json["group"] is None


def test_mail_already_out_of_the_inbox_is_left_where_it_is(on, world):
    judged(world, IN_ARCHIVE)
    mark = world.mark()
    round_(on)
    assert world.writes(mark) == []


def test_with_ai_off_it_files_nothing(app, world, monkeypatch):
    judged(world, SAFE)
    assert app.post("/api/preferences", {"auto_tidy": True}).status == 200
    app.clock.move_to(MORNING)
    round_(app)
    assert in_inbox(world, SAFE)


def test_after_days_away_it_files_nothing(on, world):
    judged(world, SAFE)
    on.clock.move_to(MORNING + timedelta(days=2))           # nobody here since yesterday's yesterday
    round_(on)
    assert in_inbox(world, SAFE)
    assistant.governor().saw_someone()
    round_(on)
    assert not in_inbox(world, SAFE)


def test_it_yields_to_someone_at_work(on, world):
    judged(world, SAFE)
    with assistant.governor().call("interactive", "ask", "m", assistant.gateway()._model()):
        round_(on)
    assert in_inbox(world, SAFE)


def test_it_is_not_what_you_handled(on, world, ai):
    judged(world, SAFE)
    round_(on)
    ai.queue(fa.answer({"narrative": "A steady week: you kept up and little is left waiting.", "next": []}))
    assert on.post("/api/review", {}).json["stats"]["handled"] == 0


def test_a_person_archiving_the_same_day_still_counts(on, world, ai):
    judged(world, SAFE)
    round_(on)
    assert on.post("/api/archive", world.copy(TOO)).status == 200
    on.settle()
    ai.queue(fa.answer({"narrative": "A steady week: you kept up and little is left waiting.", "next": []}))
    assert on.post("/api/review", {}).json["stats"]["handled"] == 1


# ── review and undo ──────────────────────────────────────────────────────────────────────────────

def test_undo_takes_the_whole_run_back(on, world):
    judged(world, SAFE)
    judged(world, TOO)
    round_(on)
    r = on.post("/api/tidy/undo", {"group": on.get("/api/tidy").json["group"]})
    assert (r.status, r.json["reversed"], r.json["failed"]) == (200, 2, 0)
    assert in_inbox(world, SAFE) and in_inbox(world, TOO)
    assert on.get("/api/tidy").json["group"] is None


def test_undo_reaches_a_run_something_was_done_after(on, world):
    judged(world, SAFE)
    round_(on)
    assert on.post("/api/archive", world.copy(ASKS)).status == 200
    on.settle()
    assert on.post("/api/tidy/undo", {"group": "tidy-2026-11-03"}).status == 200
    assert in_inbox(world, SAFE)
    assert not in_inbox(world, ASKS)
    assert on.post("/api/undo", {}).status == 200           # what came after is still there to undo
    assert in_inbox(world, ASKS)


def test_the_keyboards_undo_takes_it_back_as_one(on, world):
    judged(world, SAFE)
    judged(world, TOO)
    round_(on)
    assert on.post("/api/undo", {}).status == 200
    assert in_inbox(world, SAFE) and in_inbox(world, TOO)
    assert on.get("/api/tidy").json["group"] is None


def test_what_was_taken_back_isnt_filed_again(on, world):
    judged(world, SAFE)
    round_(on)
    on.post("/api/tidy/undo", {"group": "tidy-2026-11-03"})
    on.clock.move_to(MORNING + timedelta(days=1))
    assistant.governor().saw_someone()
    round_(on)
    assert in_inbox(world, SAFE)


def test_a_run_too_old_to_undo_no_longer_shows(on, world):
    judged(world, SAFE)
    round_(on)
    on.clock.shift(timedelta(hours=25))
    assert on.get("/api/tidy").json["group"] is None


def test_undo_only_answers_for_a_run_of_tidy(on, world):
    assert on.post("/api/archive", world.copy(SAFE)).status == 200
    on.settle()
    r = on.post("/api/tidy/undo", {"group": "not-a-run"})
    assert r.status == 400
    assert not in_inbox(world, SAFE)
    assert on.post("/api/tidy/undo", {"group": "tidy-2026-11-03"}).status == 404


def test_a_preference_nothing_else_can_be(app):
    assert app.post("/api/preferences", {"auto_tidy": "yes"}).status == 400
    assert app.get("/api/preferences").json["preferences"]["auto_tidy"] is False
