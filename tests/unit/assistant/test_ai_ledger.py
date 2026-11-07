"""The AI usage ledger: what each call to the Claude API cost, counted by local day, feature, the
model asked, the model that served it and the outcome (migration 21's ai_usage). Prices are
Anthropic's list prices, in exact integer micro-dollars. Every test here uses the fake Claude API
and fictional mail; the token counts are made up."""
from datetime import datetime, timedelta, timezone

import pytest

from fakes import anthropic_api as fa
from fixtures import world as w
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


# ── prices ───────────────────────────────────────────────────────────────────

def test_a_call_costs_its_tokens_at_the_list_price():
    from mailmind.assistant.domain.pricing import cost_micros
    assert cost_micros("claude-opus-5-5", "claude-opus-5-5", input_tokens=1_000_000) == 4_000_000
    assert cost_micros("claude-opus-5-5", "claude-opus-5-5", output_tokens=1_000_000) == 20_000_000
    assert cost_micros("claude-opus-5-5", "claude-opus-5-5", 1000, 500, 200, 1000) == 15_200   # $0.0152
    assert cost_micros("claude-sonnet-5-5", "claude-sonnet-5-5", 1000, 500) == 7_000


def test_a_cache_write_costs_a_quarter_more_than_input_and_a_read_its_own_price():
    from mailmind.assistant.domain.pricing import cost_micros
    assert cost_micros("claude-opus-5-5", "", cache_write_tokens=1_000_000) == 5_000_000
    assert cost_micros("claude-opus-5-5", "", cache_read_tokens=1_000_000) == 200_000
    assert cost_micros("claude-haiku-5-5", "", cache_write_tokens=100_000) == 12_500


def test_haiku_costs_more_past_a_hundred_thousand_prompt_tokens():
    from mailmind.assistant.domain.pricing import cost_micros
    assert cost_micros("claude-haiku-5-5", "", input_tokens=100_000, output_tokens=1000) == 10_500
    assert cost_micros("claude-haiku-5-5", "", input_tokens=100_001, output_tokens=1000) == 52_501


def test_a_fraction_of_a_micro_dollar_rounds_up():
    from mailmind.assistant.domain.pricing import cost_micros
    assert cost_micros("claude-haiku-5-5", "", input_tokens=1, output_tokens=1) == 1


def test_a_dated_model_is_priced_as_its_name_and_an_unknown_one_as_the_dearest():
    from mailmind.assistant.domain.pricing import DEAREST, PRICES, price
    assert price("claude-haiku-4-5-20251001") == PRICES["claude-haiku-4-5"]
    assert price("claude-opus-5-5") == PRICES["claude-opus-5-5"]          # not claude-opus-5's
    assert price("claude-opus-5-20261101") == PRICES["claude-opus-5"]
    assert price("claude-unknown-9") == DEAREST


def test_a_fallback_is_priced_at_the_dearer_of_the_two_models():
    from mailmind.assistant.domain.pricing import cost_micros
    assert cost_micros("claude-opus-5-5", "claude-opus-4-8", 1000, 100) == 7_500     # at Opus 4.8's
    assert cost_micros("claude-opus-4-8", "claude-opus-5-5", 1000, 100) == 7_500


# ── the ledger ───────────────────────────────────────────────────────────────

@pytest.fixture
def clock():
    """The clock frozen at Monday noon."""
    import time_machine
    with time_machine.travel(MONDAY_NOON, tick=False) as traveller:
        yield traveller


@pytest.fixture(params=["sdk", "urllib"])
def gateway(request, data_dir, ai, clock, monkeypatch):
    """The app's gateway, through each transport, with the ledger in this test's database."""
    from mailmind.bootstrap import assistant
    monkeypatch.setattr(assistant.status, "_last", {"ok": None, "ts": 0, "error": ""})
    assistant.use(request.param)
    yield assistant.gateway()
    assistant.use("urllib")


def test_each_call_adds_to_its_days_row(gateway, ai):
    ai.queue(answer("a", 1000, 200), answer("b", 3000, 100, cache_read=2000))
    gateway.ask("Catch me up.", feature="thread")
    gateway.ask("Catch me up again.", feature="thread")
    [row] = rows()
    assert row == {"day": "2026-11-02", "feature": "thread", "model": "claude-opus-5-5",
                   "served": "claude-opus-5-5", "outcome": "ok", "calls": 2, "input_tokens": 4000,
                   "output_tokens": 300, "cache_write_tokens": 0, "cache_read_tokens": 2000,
                   "cost_micros": 4000 * 4 + 300 * 20 + 2000 // 5}


def test_each_outcome_has_its_own_row_and_a_call_that_got_no_reply_costs_nothing(gateway, ai):
    from mailmind.assistant.application.ports import ModelError
    retry_now = {"retry-after": "0", "retry-after-ms": "1"}
    busy = fa.error(529, "overloaded_error", "Overloaded", retry_now)
    ai.queue(answer("fine", 100, 10),
             (200, {"stop_reason": "refusal", "content": [], "usage": {"input_tokens": 50, "output_tokens": 0}}),
             fa.error(400, "invalid_request_error", "bad request"),
             busy, busy, busy)
    gateway.ask("one", feature="ask")
    assert gateway.ask("two", feature="ask").refused
    for text in ("three", "four"):
        with pytest.raises(ModelError):
            gateway.ask(text, feature="ask")
    got = {r["outcome"]: (r["calls"], r["served"], r["cost_micros"]) for r in rows()}
    assert got == {"ok": (1, "claude-opus-5-5", 600), "refused": (1, "claude-opus-5-5", 200),
                   "rejected": (1, "", 0), "failed": (1, "", 0)}


def test_ai_off_counts_nothing(data_dir, clock, anthropic_api):
    from mailmind.assistant.application.ports import AIOff
    from mailmind.bootstrap import assistant
    with pytest.raises(AIOff):
        assistant.gateway().ask("Catch me up.", feature="thread")
    assert rows() == [] and anthropic_api.requests == []


def test_a_fallback_is_counted_under_the_model_that_served_it(gateway, ai):
    ai.queue(answer("whole", 1000, 100, content=[
        {"type": "text", "text": "half"},
        {"type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "claude-opus-4-8"},
         "trigger": {"type": "refusal"}},
        {"type": "text", "text": "whole"}]))
    assert gateway.ask("Catch me up.", feature="thread").text == "whole"
    [row] = rows()
    assert (row["model"], row["served"], row["cost_micros"]) == ("claude-opus-5-5", "claude-opus-4-8", 7_500)


def test_calls_count_against_the_local_day_they_ended_on(gateway, ai, clock):
    ai.queue(answer("late", 1000), answer("early", 1000))
    clock.move_to(datetime(2026, 11, 2, 23, 59, 30, tzinfo=timezone.utc))
    gateway.ask("one", feature="triage")
    clock.move_to(datetime(2026, 11, 3, 0, 0, 30, tzinfo=timezone.utc))
    gateway.ask("two", feature="triage")
    assert [(r["day"], r["calls"]) for r in rows()] == [("2026-11-02", 1), ("2026-11-03", 1)]
    assert gateway.spent_today() == 4000


def test_spent_today_and_the_days_report_add_up_the_features(gateway, ai, clock):
    ai.queue(answer("t", 1000), answer("t", 1000), answer("d", 2000, 500))
    gateway.ask("one", feature="triage")
    gateway.ask("two", feature="triage")
    gateway.ask("three", feature="draft")
    clock.shift(timedelta(days=1))
    assert gateway.spent_today() == 0
    report = gateway.usage(days=2)
    assert [(d["day"], d["calls"], d["cost_micros"]) for d in report] == [("2026-11-03", 0, 0),
                                                                          ("2026-11-02", 3, 26_000)]
    assert report[1]["features"] == {"draft": {"calls": 1, "cost_micros": 18_000},
                                     "triage": {"calls": 2, "cost_micros": 8_000}}


def test_a_ledger_that_fails_never_fails_the_call(gateway, ai, monkeypatch, capsys):
    import sqlite3
    from mailmind.bootstrap import assistant

    def broken(*a, **kw):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(assistant.LEDGER, "record", broken)
    assert gateway.ask("Catch me up.", feature="thread").text == fa.STUB_TEXT
    assert "[ai] the usage ledger failed: OperationalError" in capsys.readouterr().out


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
    assert app.post("/api/ai/summarize", {"pk": lunch}).status == 200
    assert app.post("/api/briefing", {}).status == 200
    assert app.post("/api/briefing/since", {"since": 0}).status == 200
    assert app.post("/api/review", {"account": w.ADA}).status == 200
    assert app.post("/api/ai/draft-reply", {"pk": lunch, "tone": "warm"}).status == 200
    app.clock.shift(timedelta(seconds=46))         # drafting ahead yields to the calls just made
    assert app.get(f"/api/ai/draft-ahead?pk={lunch}").json["status"] == "generating"
    app.settle()
    assert app.post("/api/ai/rewrite", {"op": "shorten", "text": "see you thursday"}).status == 200
    assert app.post("/api/ai/ask", {"question": "When is lunch with Lin?"}).status == 200
    assert app.post("/api/ai/do-plan", {"cmd": "archive the newsletters"}).status == 200
    counted = {r["feature"]: r["calls"] for r in rows()}
    assert counted == {"triage": 1, "thread": 1, "brief": 1, "delta": 1, "review": 1, "draft": 1,
                       "draft-ahead": 1, "rewrite": 1, "ask": 1, "plan": 1}
    assert sum(counted.values()) == len(ai.requests)


# ── what the ledger reports ──────────────────────────────────────────────────

def test_dollars_round_up_to_the_cent():
    from mailmind.assistant.domain.pricing import usd
    assert [usd(m) for m in (0, 1, 10_000, 15_200, 1_234_567_890)] == [
        "$0.00", "$0.01", "$0.01", "$0.02", "$1,234.57"]


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


def test_a_ledger_that_cant_be_read_leaves_the_status_without_a_spend(data_dir, monkeypatch, capsys):
    import sqlite3
    from mailmind.bootstrap import assistant

    def broken(day):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(assistant.LEDGER, "spent", broken)
    assert assistant.status.report()["spent_today_micros"] is None
    assert "[ai] the usage ledger can't be read: OperationalError" in capsys.readouterr().out


def test_the_command_line_lists_each_day_by_feature_and_the_total(gateway, ai, clock, capsys):
    from mailmind.__main__ import main
    ai.queue(answer("t", 1000), answer("d", 2000, 500), answer("t", 1000))
    gateway.ask("one", feature="triage")
    gateway.ask("two", feature="draft")
    clock.shift(timedelta(days=2))
    gateway.ask("three", feature="triage")
    with pytest.raises(SystemExit) as ended:
        main(["ai", "usage", "--days", "3"])
    assert ended.value.code == 0
    assert capsys.readouterr().out.splitlines() == [
        "AI usage by local day at Anthropic's list prices, the last 3 days:",
        "  2026-11-04  $0.01      1 call   triage $0.01 (1)",
        "  2026-11-03      -",
        "  2026-11-02  $0.03      2 calls  draft $0.02 (1) · triage $0.01 (1)",
        "  in all      $0.03      3 calls"]


def test_the_command_line_reads_and_never_migrates(tmp_path, capsys):
    import sqlite3
    from mailmind.__main__ import ai_usage
    from mailmind.platform import settings
    with settings.use(settings.Settings(home=str(tmp_path))):
        db = settings.current().db
        assert ai_usage(7) == 1
        assert not (tmp_path / "mailmind.db").exists()
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE messages (pk INTEGER PRIMARY KEY)")
        c.execute("PRAGMA user_version=20")
        c.commit()
        c.close()
        assert ai_usage(7) == 1
        c = sqlite3.connect(db)
        try:
            assert c.execute("PRAGMA user_version").fetchone()[0] == 20
            assert [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")] == ["messages"]
        finally:
            c.close()
    assert capsys.readouterr().out.splitlines() == [
        "There is no database yet, so no usage either.",
        "The database predates the usage ledger: start the app once and it will be added."]
