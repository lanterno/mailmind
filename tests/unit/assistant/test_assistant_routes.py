"""The Assistant's routes on their router: what a call never sent answers, what is only a read, its routes' own words and
statuses, and what the schemas refuse and let by."""
import json
import time

import pytest

from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.assistant.api import AIOff, OverLimit
from mailmind.assistant.application.gateway import local_day
from mailmind.assistant.application.ports import Spend, Usage
from mailmind.bootstrap import assistant, mailbox, web
from mailmind.bootstrap.assistant import answers
from mailmind.bootstrap.mailbox import store
from mailmind.kernel.errors import Conflict, NotFound, Refused, Unreachable

BAD_REQUEST = {"ok": False, "error": "bad request"}
AI_OFF = {"ok": False, "error": "AI is off: MailMind has no Claude API key", "ai_off": True}
LUNCH = "ada-inbox-02-lunch-confirm.eml"        # Lin's last word in Ada's lunch thread
VENUE_END = "cy-inbox-02-thread-end.eml"        # Mae's last word in the venue thread
REWRITE = {"op": "shorten", "text": "see you thursday"}
# Every route that spends, with what it takes to get as far as a call.
SPENDS = [("/api/ai/summarize", {"pk": LUNCH}), ("/api/ai/draft-reply", {"pk": LUNCH, "tone": "warm"}), ("/api/ai/rewrite", REWRITE),
          ("/api/ai/ask", {"question": "When is lunch with Lin?"}), ("/api/ai/do-plan", {"cmd": "archive the newsletters"}),
          ("/api/briefing", {}), ("/api/briefing/refresh", {}), ("/api/briefing/since", {"since": 0}), ("/api/review", {}),
          ("/api/review/refresh", {})]
# Each with its own words for what it can't explain, and what it asks to have that said of.
WORDS = [("/api/ai/summarize", {}, "asking", "summarize", "summary failed"), ("/api/ai/draft-reply", {}, "drafting", "reply", "draft failed"),
         ("/api/ai/rewrite", {}, "asking", "rewrite", "rewrite failed"), ("/api/ai/ask", {}, "asking", "ask", "ask failed"),
         ("/api/ai/do-plan", {}, "bulk", "plan", "plan failed"), ("/api/briefing", {}, "asking", "briefing", "briefing failed"),
         ("/api/briefing/refresh", {}, "asking", "briefing", "briefing failed"), ("/api/briefing/since", {}, "asking", "delta", "delta failed"),
         ("/api/review", {}, "asking", "review", "review failed"), ("/api/review/refresh", {}, "asking", "review", "review failed")]


def triaged(pk):
    c = store.conn()
    try:
        answers.save_summary(c, pk, "Lin confirms lunch.", 5, "action-needed", "Reply", "claude-opus-5-5", "2026-11-02T11:00:00+00:00")
        c.commit()
    finally:
        c.close()


def ready_to_call(world, body):
    """`body`, naming a real message, with one triaged, so a brief, a delta and a review all have something to call about."""
    lunch = world.pk(LUNCH)
    triaged(lunch)
    return {**body, "pk": lunch} if body.get("pk") == LUNCH else body


def spend(micros):
    assistant.LEDGER.record(Spend(day=local_day(time.time()), feature="ask", model="claude-opus-5-5", served="claude-opus-5-5", outcome="ok",
                                  usage=Usage(), cost_micros=micros, ts=int(time.time())))


# ── a call that was never sent ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path, body", SPENDS)
def test_at_the_limit_a_route_asks_and_with_leave_it_goes(app, world, ai, path, body):
    body = ready_to_call(world, body)
    spend(15_000_000)
    r = app.post(path, body)
    assert (r.status, r.json["ok"], r.json["over_limit"], r.json["limit_micros"]) == (402, False, True, 15_000_000)
    assert ai.requests == []
    assert app.post(path, body, headers={web.AI_OVER_HEADER: "no"}).status == 402
    assert ai.requests == []
    r = app.post(path, body, headers={web.AI_OVER_HEADER: "allow"})
    assert (r.status, r.json["ok"]) == (200, True)
    assert ai.requests


@pytest.mark.parametrize("path, body", SPENDS)
def test_with_no_key_a_route_says_ai_is_off(app, world, path, body):
    r = app.post(path, ready_to_call(world, body))
    assert (r.status, r.json) == (409, AI_OFF)


@pytest.mark.parametrize("path, body", SPENDS)
def test_when_the_model_fails_a_route_says_asking_again_can_help(app, world, ai, path, body):
    body = ready_to_call(world, body)
    ai.queue(fa.error(400, "invalid_request_error", "bad request"))
    r = app.post(path, body)
    assert (r.status, r.json["ok"], r.json["retry"]) == (502, False, True)


# ── what a route's own catch lets through ────────────────────────────────────────────────────────

@pytest.mark.parametrize("path, body, on, method, failed", WORDS)
def test_what_a_route_cant_explain_goes_in_its_own_words(app, monkeypatch, path, body, on, method, failed):
    def broken(*args, **kwargs):
        raise RuntimeError("what a server said")
    monkeypatch.setattr(getattr(assistant, on), method, broken)
    r = app.post(path, body)
    assert (r.status, r.json) == (500, {"ok": False, "error": failed})


@pytest.mark.parametrize("raised, status, said", [(OverLimit(7, 5), 402, {"over_limit": True, "spent_micros": 7, "limit_micros": 5,
                                                                         "error": "today's AI spending has reached the daily limit"}),
                                                  (AIOff("off"), 409, {"ai_off": True, "error": "AI is off: MailMind has no Claude API key"}),
                                                  (NotFound("gone"), 404, {"error": "gone"}), (Refused(), 400, {"error": "bad request"}),
                                                  (Conflict("moved"), 409, {"error": "moved"}),
                                                  (Unreachable("away", retry=True), 502, {"error": "away", "retry": True})],
                         ids=lambda v: type(v).__name__ if isinstance(v, Exception) else "")
@pytest.mark.parametrize("path, body, on, method, failed", WORDS)
def test_what_the_route_class_answers_gets_past_a_routes_own_catch(app, monkeypatch, path, body, on, method, failed, raised, status, said):
    def raises(*args, **kwargs):
        raise raised
    monkeypatch.setattr(getattr(assistant, on), method, raises)
    r = app.post(path, body)
    assert (r.status, r.json) == (status, {"ok": False, **said})


# ── a GET reads, and a POST writes ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path, missing", [("/api/briefing", {"ok": True, "missing": True, "narrative": "", "groups": [], "cached": False, "stale": False}),
                                           ("/api/review", {"ok": True, "missing": True})])
def test_a_get_with_nothing_stored_calls_nothing_and_a_post_writes_it(app, world, ai, path, missing):
    triaged(world.pk(LUNCH))
    r = app.get(path)
    assert (r.status, r.json, ai.requests) == (200, missing, [])
    written = app.post(path, {})
    assert (written.status, written.json["cached"], len(ai.requests)) == (200, False, 1)
    stored = app.get(path)
    assert (stored.status, stored.json["cached"], len(ai.requests)) == (200, True, 1)
    assert stored.json["narrative"] == written.json["narrative"]


def test_a_delta_read_calls_nothing_and_its_post_writes_it(app, world, ai):
    triaged(world.pk(LUNCH))
    r = app.get("/api/briefing/since?since=0")
    assert (r.status, r.json["empty"], r.json["missing"], ai.requests) == (200, False, True, [])
    written = app.post("/api/briefing/since", {"since": 0})
    assert (written.status, written.json["cached"], len(ai.requests)) == (200, False, 1)
    stored = app.get("/api/briefing/since?since=0")
    assert (stored.status, stored.json["cached"], len(ai.requests)) == (200, True, 1)


@pytest.mark.parametrize("path", ["/api/briefing/refresh", "/api/review/refresh", "/api/ai/summarize", "/api/ai/draft-reply", "/api/ai/rewrite",
                                  "/api/ai/ask", "/api/ai/do-plan", "/api/ai/do-confirm"])
def test_what_spends_or_acts_has_no_get(app, ai, path):
    r = app.get(path)
    assert (r.status, r.body, ai.requests) == (404, b"not found", [])


def test_drafting_ahead_has_no_post(app, ai):
    assert app.post("/api/ai/draft-ahead", {}).status == 404


@pytest.mark.parametrize("headers", [{"X-CSRF-Token": None}, {"X-CSRF-Token": "wrong"}, {"X-CSRF-Token": ""}])
def test_only_the_pages_token_starts_a_draft_ahead(app, world, ai, headers):
    pk = world.pk(VENUE_END)
    triaged(pk)
    r = app.get(f"/api/ai/draft-ahead?pk={pk}", headers=headers)
    assert (r.status, r.json) == (200, {"ok": True, "status": "none"})
    app.settle()
    assert ai.requests == []
    assert app.get(f"/api/ai/draft-ahead?pk={pk}&peek=1").json == {"ok": True, "status": "none"}
    app.settle()
    assert ai.requests == []
    assert app.get(f"/api/ai/draft-ahead?pk={pk}").json["status"] == "generating"
    app.settle()
    assert len(ai.requests) == 1
    r = app.get(f"/api/ai/draft-ahead?pk={pk}", headers=headers)
    assert (r.json["status"], r.json["body"]) == ("ready", "stub body")


@pytest.mark.parametrize("query", ["", "?pk=", "?peek=1", "?pk=999999"])
def test_drafting_ahead_for_no_message(app, query):
    assert app.get("/api/ai/draft-ahead" + query).json == {"ok": True, "status": "none"}


def test_asking_after_a_draft_ahead_counts_as_no_one(app, monkeypatch):
    """A hidden tab asks: that says nothing of anyone being here. Reading the brief does."""
    seen = []
    monkeypatch.setattr(assistant.governor(), "saw_someone", lambda: seen.append(1))
    assert app.get("/api/ai/draft-ahead").status == 200
    assert seen == []
    assert app.get("/api/briefing").status == 200
    assert seen == [1]


# ── a route's other statuses ─────────────────────────────────────────────────────────────────────

def test_a_summary_still_going_is_the_504(app, world, ai, monkeypatch):
    monkeypatch.setattr(assistant.threads._FLIGHTS, "run", lambda *args, **kwargs: None)
    r = app.post("/api/ai/summarize", {"pk": world.pk(LUNCH)})
    assert (r.status, r.json) == (504, {"ok": False, "error": "still summarizing"})


@pytest.mark.parametrize("body", [{}, {"plan": ""}, {"plan": None}, {"plan": "made-up"}])
def test_a_plan_no_one_made_is_the_410(app, world, body):
    since = world.mark()
    r = app.post("/api/ai/do-confirm", body)
    assert (r.status, r.json) == (410, {"ok": False, "error": "this plan has expired", "expired": True})
    assert world.writes(since) == []


@pytest.mark.parametrize("given, group", [({"undo_group": "g"}, "g"), ({"undo_group": None}, None), ({}, None)])
def test_a_confirm_hands_on_its_undo_group(app, ai, monkeypatch, given, group):
    ai.queue(fa.answer({"intent": "act", "verb": "archive", "filter": "from:lin"}))
    plan = app.post("/api/ai/do-plan", {"cmd": "archive what Lin sent"}).json
    asked = []

    def carry_out(verb, preset, messages, undo_group):
        asked.append((verb, undo_group))
        return {"ok": True, "verb": verb, "count": 0, "failed": 0, "gone": 0, "queued": 0, "undoable": False}
    monkeypatch.setattr(mailbox.filing, "carry_out", carry_out)
    assert app.post("/api/ai/do-confirm", {"plan": plan["plan"], **given}).status == 200
    assert asked == [("archive", group)]


# ── what the schemas refuse, and what they let by ────────────────────────────────────────────────

@pytest.mark.parametrize("path, body", [
    ("/api/ai/summarize", {"pk": 1, "force": "yes"}),
    ("/api/ai/summarize", {"pk": 1, "force": 1}),
    ("/api/ai/draft-reply", {"pk": 1, "tone": 5}),
    ("/api/ai/draft-reply", {"pk": 1, "instruction": ["x"]}),
    ("/api/ai/rewrite", {**REWRITE, "op": 5}),
    ("/api/ai/rewrite", {**REWRITE, "lang": 5}),
    ("/api/ai/rewrite", {**REWRITE, "text": ["x"]}),
    ("/api/ai/ask", {"question": 5}),
    ("/api/ai/do-plan", {"cmd": 5}),
    ("/api/ai/do-confirm", {"plan": 5}),
    ("/api/ai/do-confirm", {"plan": ["x"]}),
    ("/api/ai/do-confirm", {"plan": {"plan": "x"}}),
    ("/api/ai/do-confirm", {"plan": "x", "undo_group": 5}),
    ("/api/briefing/since", {"since": [1]}),
    ("/api/briefing/since", {"since": {"a": 1}}),
])
def test_a_value_its_model_refuses_is_the_400_and_calls_nothing(app, world, ai, path, body):
    since = world.mark()
    r = app.post(path, body)
    assert (r.status, r.json, ai.requests) == (400, BAD_REQUEST, [])
    assert world.writes(since) == []


@pytest.mark.parametrize("path", ["/api/briefing", "/api/briefing/refresh", "/api/review", "/api/review/refresh", "/api/briefing/since",
                                  "/api/ai/ask", "/api/ai/do-plan", "/api/ai/do-confirm"])
@pytest.mark.parametrize("not_an_object", [[1], "text", 5, None])
def test_a_body_that_isnt_an_object_is_the_400(app, ai, path, not_an_object):
    r = app.post(path, json.dumps(not_an_object).encode())
    assert (r.status, r.json, ai.requests) == (400, BAD_REQUEST, [])


@pytest.mark.parametrize("since", ["soon", "", None, 0, 12.5, "12.5", True])
def test_a_time_since_that_isnt_one_reads_as_none(app, ai, since):
    """The use case holds each to the last day, a time it can't read as well: an answer every time, and never a refusal."""
    r = app.post("/api/briefing/since", {"since": since})
    assert (r.status, r.json["ok"]) == (200, True)


@pytest.mark.parametrize("query", ["", "?since=soon", "?since=", "?since=0&since=x", "?account=nobody@example.test", "?account="])
def test_a_delta_reads_its_query_string_as_it_did(app, query):
    r = app.get("/api/briefing/since" + query)
    assert (r.status, r.json["ok"]) == (200, True)


def test_a_delta_goes_by_the_account_it_names_or_else_by_all_of_them(app, ai):
    def count(query):
        return app.get("/api/briefing/since?since=0" + query).json["count"]
    every = count("")
    assert 0 < count(f"&account={w.ADA}") < every
    assert count("&account=nobody@example.test") == every
    assert app.post("/api/briefing/since", {"since": 0, "account": w.ADA}).json["count"] == count(f"&account={w.ADA}")


def test_a_field_nothing_here_reads_is_nothing_to_refuse(app, ai):
    r = app.post("/api/ai/rewrite", {**REWRITE, "subject": 5, "account": 5})
    assert (r.status, r.json["ok"]) == (200, True)
    assert app.post("/api/briefing", {"account": 5}).status == 200
