"""The cost governor: how many AI calls go at once, when background and speculative work waits, the
daily limit (decision D7: $15 unless the config names another), and the breaker that stops
background work the API keeps rejecting. The unit tests drive a CostGovernor on a clock of their
own; the route tests use the fake Claude API and fictional mail."""
import json
from datetime import timedelta

import pytest

import problems
from mailmind.assistant.application.governor import (YIELD_SECONDS,
                                                     CostGovernor)
from mailmind.bootstrap.assistant import answers
from mailmind.bootstrap import assistant
from mailmind.bootstrap import web


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now

    def shift(self, seconds):
        self.now += seconds


def governor(spent=0, limit=10_000):
    """A governor with today's spending in `box["spent"]` (an exception there: the ledger can't be
    read), on a clock of its own."""
    clock, box = Clock(), {"spent": spent}

    def read():
        if isinstance(box["spent"], Exception):
            raise box["spent"]
        return box["spent"]
    return CostGovernor(spent=read, limit=lambda: limit, clock=clock), clock, box


# ── through the routes ───────────────────────────────────────────────────────

def with_limit(world, usd):
    path = world.dir / "mailmind.config.json"
    body = json.loads(path.read_text())
    body["ai_daily_limit_usd"] = usd
    path.write_text(json.dumps(body))


def spend(micros):
    """Count `micros` against today, as calls already made would."""
    import time
    from mailmind.assistant.application.gateway import local_day
    from mailmind.assistant.application.ports import Spend, Usage
    from mailmind.bootstrap import assistant
    assistant.LEDGER.record(Spend(day=local_day(time.time()), feature="ask", model="claude-opus-5-5",
                                  served="claude-opus-5-5", outcome="ok", usage=Usage(),
                                  cost_micros=micros, ts=int(time.time())))


REWRITE = {"op": "shorten", "text": "see you thursday"}


def test_at_the_limit_an_interactive_request_asks_first_and_goes_when_allowed(app, world, ai):
    from fakes import anthropic_api as fa
    with_limit(world, 0.01)                                # 10,000 micro-dollars
    status, body = fa.answer(json.dumps({"text": "Thursday."}))
    body["usage"] = {"input_tokens": 2000, "output_tokens": 200,
                     "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    ai.queue((status, body))
    assert app.post("/api/ai/rewrite", REWRITE).status == 200      # $0.012: now over
    sent = len(ai.requests)
    r = app.post("/api/ai/rewrite", REWRITE)
    assert (r.status, r.json) == (402, problems.over_limit(12_000, 10_000))
    assert len(ai.requests) == sent                        # nothing was sent
    ai_line = app.get("/api/digest").json["ai"]
    assert (ai_line["last_ok"], ai_line["last_error"]) == (True, "")   # held back is not a failure
    r = app.post("/api/ai/rewrite", REWRITE, headers={web.AI_OVER_HEADER: "allow"})
    assert r.status == 200 and len(ai.requests) == sent + 1
    assert app.post("/api/ai/rewrite", REWRITE, headers={web.AI_OVER_HEADER: "yes"}).status == 402


LUNCH = "ada-inbox-02-lunch-confirm.eml"        # Lin's last word in Ada's lunch thread


@pytest.mark.parametrize("path, body", [
    ("/api/ai/summarize", {"key": LUNCH}),
    ("/api/ai/draft-reply", {"key": LUNCH, "tone": "warm"}),
    ("/api/ai/rewrite", REWRITE),
    ("/api/ai/ask", {"question": "When is lunch with Lin?"}),
    ("/api/ai/do-plan", {"cmd": "archive the newsletters"}),
    ("/api/briefing/refresh", {}),
    ("/api/review/refresh", {}),
    ("/api/briefing", {}),
    ("/api/briefing/since", {"since": 0}),
    ("/api/review", {})])
def test_every_ai_request_someone_makes_asks_at_the_limit(app, world, ai, path, body):
    from mailmind.bootstrap.mailbox import store
    lunch = world.pk(LUNCH)
    if body.get("key") == LUNCH:
        body = {**body, **world.copy(LUNCH)}
    c = store.conn()
    try:                                               # triaged, so the brief has something to say
        answers.save_summary(c, lunch, "Lin confirms lunch.", 5, "action-needed", "Reply", "claude-opus-5-5",
                           "2026-11-02T11:00:00+00:00")
        c.commit()
    finally:
        c.close()
    spend(15_000_000)
    r = app.post(path, body)
    assert (r.status, r.json["over_limit"]) == (402, True)
    assert ai.requests == []


def test_at_the_limit_reading_the_ai_cards_never_asks(app, world, ai):
    spend(15_000_000)
    for path in ("/api/briefing", "/api/review", "/api/briefing/since?since=0"):
        assert app.get(path).status == 200                 # a GET only reads what is stored
    assert ai.requests == []


def test_at_the_limit_triage_waits_for_tomorrow(app, world, ai):
    spend(15_000_000)                                      # the default limit, $15
    assert app.get("/api/debug/ai").json["governor"]["spent_today_micros"] == 15_000_000
    assistant.assessments.round()
    assert ai.requests == []
    app.clock.shift(timedelta(days=1))
    assistant.assessments.round()
    assert ai.requests                                     # a new day: triage goes again


def test_the_debug_route_shows_the_governor(app):
    assert app.get("/api/debug/ai").json["governor"] == {
        "spent_today_micros": 0, "limit_micros": 15_000_000, "reserved_micros": 0, "interactive_running": 0,
        "since_interactive": None, "since_seen": 0.0, "breaker": []}


def test_a_refused_key_stops_triage_but_not_a_click(app, world, ai):
    from fakes import anthropic_api as fa
    ai.responder = lambda req: fa.error(401, "authentication_error", "invalid x-api-key")
    app.post("/api/ai/rewrite", REWRITE)
    assert len(ai.requests) == 1
    app.clock.shift(timedelta(seconds=YIELD_SECONDS))
    sent = len(ai.requests)
    assistant.assessments.round()
    assert len(ai.requests) == sent                        # the breaker is open for background work
    assert app.get("/api/debug/ai").json["governor"]["breaker"] == ["every feature"]
    app.post("/api/ai/rewrite", REWRITE)
    assert len(ai.requests) == sent + 1                    # a click still goes, and shows the error
