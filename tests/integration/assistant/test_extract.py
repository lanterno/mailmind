"""A message's proposals, read by Claude on a click: what the model answers is read with care, and nothing is created by it."""


from fakes import anthropic_api as fa
from mailmind.bootstrap import accounts
from mailmind.bootstrap.mailbox import store
from mailmind.bootstrap import mailbox
from fixtures import world as w

LUNCH = "ada-inbox-02-lunch-confirm.eml"
MEETING = {"title": "Design review", "start": "2026-11-12T15:00", "end": "2026-11-12T16:00", "place": "Room 2"}


def q(sql, *params):
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, params)]
    finally:
        c.close()


def test_the_route_answers_suggestions_and_makes_nothing(app, world, ai):
    ai.queue(fa.answer({"events": [MEETING], "tasks": [{"title": "Send the slides", "due": "2026-11-09"}]}))
    r = app.post("/api/ai/extract", world.copy(LUNCH))
    assert r.status == 200
    assert r.json["events"] == [{**MEETING, "all_day": False}]
    assert r.json["tasks"] == [{"title": "Send the slides", "due": "2026-11-09"}]
    assert q("SELECT * FROM tasks") == []
    assert app.get("/api/tasks").json["items"] == []


def test_a_renumber_leaves_what_the_key_names(app, world, ai):
    ai.queue(fa.answer({"events": [], "tasks": []}))
    copy = world.copy(LUNCH)
    world.server.renumber(w.ADA, "INBOX")
    mailbox.ingest.sync_new(accounts.get(w.ADA))
    assert app.post("/api/ai/extract", {"key": copy["key"], "folder": "INBOX"}).status == 200


def test_a_key_with_no_copy_left_is_not_found_and_asks_nothing(app, ai):
    r = app.post("/api/ai/extract", {"key": 987654})
    assert (r.status, r.json["detail"]) == (404, "not found") and not ai.requests


def test_with_ai_off_it_says_so(app, world):
    r = app.post("/api/ai/extract", world.copy(LUNCH))
    assert (r.status, r.json["ai_off"]) == (409, True)


def test_a_call_that_fails_says_a_retry_can_help(app, world, ai):
    ai.responder = lambda req: fa.error(400, "invalid_request_error", "no")
    r = app.post("/api/ai/extract", world.copy(LUNCH))
    assert (r.status, r.json["retry"]) == (502, True)
