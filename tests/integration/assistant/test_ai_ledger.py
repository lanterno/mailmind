"""The AI usage ledger: what each call to the Claude API cost, counted by local day, feature, the
model asked, the model that served it and the outcome (migration 21's ai_usage). Prices are
Anthropic's list prices, in exact integer micro-dollars. Every test here uses the fake Claude API
and fictional mail; the token counts are made up."""
from datetime import datetime, timedelta, timezone

import pytest

from fakes import anthropic_api as fa
from mailmind.bootstrap.assistant import answers

LUNCH = "ada-inbox-02-lunch-confirm.eml"        # Lin's last word in Ada's lunch thread
# conftest.NOW, Monday 2 November 2026 at noon UTC (and TZ is UTC). Not imported: under importlib
# mode `import conftest` runs conftest.py again, as a second module, and that takes the key out.
MONDAY_NOON = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def answer(text, input_tokens=0, output_tokens=0, cache_write=0, cache_read=0, **extra):
    status, body = fa.answer(text)
    body["usage"] = {"input_tokens": input_tokens, "output_tokens": output_tokens,
                     "cache_creation_input_tokens": cache_write, "cache_read_input_tokens": cache_read}
    return status, {**body, **extra}


def rows():
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(
            "SELECT day, feature, model, served, outcome, calls, input_tokens, output_tokens, "
            "cache_write_tokens, cache_read_tokens, cost_micros FROM ai_usage ORDER BY day, feature, outcome")]
    finally:
        c.close()


# ── the ledger ───────────────────────────────────────────────────────────────

@pytest.fixture
def clock():
    """The clock frozen at Monday noon."""
    import time_machine
    with time_machine.travel(MONDAY_NOON, tick=False) as traveller:
        yield traveller


# ── every feature names its calls ─────────────────────────────────────────────

def summarize_as(pk, importance, category):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        answers.save_summary(c, pk, "Stub triage.", importance, category, "Reply", "claude-opus-5-5",
                           datetime.now(timezone.utc).isoformat())
        c.commit()
    finally:
        c.close()


def test_each_ai_feature_counts_its_calls_under_its_own_name(app, world, ai):
    from mailmind.bootstrap import assistant
    assistant.assessments.summarize_pending(limit=1, workers=1)
    lunch = world.pk(LUNCH)
    summarize_as(lunch, 5, "action-needed")
    assert app.post("/api/ai/summarize", world.copy(LUNCH)).status == 200
    assert app.post("/api/briefing", {}).status == 200
    assert app.post("/api/briefing/since", {"since": 0}).status == 200
    assert app.post("/api/review", {}).status == 200
    assert app.post("/api/ai/draft-reply", {**world.copy(LUNCH), "tone": "warm"}).status == 200
    app.clock.shift(timedelta(seconds=46))         # drafting ahead yields to the calls just made
    assert app.get(f"/api/ai/draft-ahead?key={world.key(LUNCH)}").json["status"] == "generating"
    app.settle()
    assert app.post("/api/ai/rewrite", {"op": "shorten", "text": "see you thursday"}).status == 200
    assert app.post("/api/ai/ask", {"question": "When is lunch with Lin?"}).status == 200
    assert app.post("/api/ai/do-plan", {"cmd": "archive the newsletters"}).status == 200
    counted = {r["feature"]: r["calls"] for r in rows()}
    assert counted == {"triage": 1, "thread": 1, "brief": 1, "delta": 1, "review": 1, "draft": 1,
                       "draft-ahead": 1, "rewrite": 1, "ask": 1, "plan": 1}
    assert sum(counted.values()) == len(ai.requests)


def test_the_status_says_what_today_cost_and_the_debug_route_lists_each_day(app, ai):
    import json
    ai.queue(answer(json.dumps({"text": "See you Thursday."}), 1000, 100))
    assert app.get("/api/digest").json["ai"]["spent_today_micros"] == 0
    assert app.post("/api/ai/rewrite", {"op": "shorten", "text": "see you thursday"}).status == 200
    assert app.get("/api/digest").json["ai"]["spent_today_micros"] == 1000 * 4 + 100 * 20
    assert app.get("/api/debug/ai?days=2").json["days"] == [
        {"day": "2026-11-02", "calls": 1, "cost_micros": 6000,
         "features": {"rewrite": {"calls": 1, "cost_micros": 6000}}},
        {"day": "2026-11-01", "calls": 0, "cost_micros": 0, "features": {}}]
    assert len(app.get("/api/debug/ai").json["days"]) == 30
    assert len(app.get("/api/debug/ai?days=0").json["days"]) == 1
    assert len(app.get("/api/debug/ai?days=5000").json["days"]) == 366
