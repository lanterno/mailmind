"""The cost governor: how many AI calls go at once, when background and speculative work waits, the
daily limit (decision D7: $15 unless the config names another), and the breaker that stops
background work the API keeps rejecting. The unit tests drive a CostGovernor on a clock of their
own; the route tests use the fake Claude API and fictional mail."""
import json
import threading

import pytest

from mailmind.assistant.application.governor import (PRESENCE_SECONDS, SCHEDULED_PRESENCE_SECONDS, TRIP_AFTER, YIELD_SECONDS,
                                                     CostGovernor, allowing_over)
from mailmind.assistant.application.ports import Deferred, ModelError, OverLimit
from mailmind.assistant.domain import features


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now

    def shift(self, seconds):
        self.now += seconds


class Model:
    """A LanguageModel stand-in: the breaker only asks whether the model in use is still this one."""


def governor(spent=0, limit=10_000):
    """A governor with today's spending in `box["spent"]` (an exception there: the ledger can't be
    read), on a clock of its own."""
    clock, box = Clock(), {"spent": spent}

    def read():
        if isinstance(box["spent"], Exception):
            raise box["spent"]
        return box["spent"]
    return CostGovernor(spent=read, limit=lambda: limit, clock=clock), clock, box


def held_calls(gov, kind, n, feature="ask"):
    """Start `n` calls of `kind` that hold their slots until released. Returns (release, threads,
    how many got in, what each raised)."""
    entered, release, raised = threading.Semaphore(0), threading.Event(), []

    def call():
        try:
            with gov.call(kind, feature, "m", MODEL):
                entered.release()
                release.wait(5)
        except Exception as e:
            raised.append(e)
    threads = [threading.Thread(target=call) for _ in range(n)]
    for t in threads:
        t.start()
    return release, threads, entered, raised


MODEL = Model()


# ── slots ────────────────────────────────────────────────────────────────────

def test_two_interactive_calls_go_at_once_and_a_third_waits_for_a_slot():
    gov, _, _ = governor()
    release, threads, entered, _ = held_calls(gov, "interactive", 3)
    assert entered.acquire(timeout=5) and entered.acquire(timeout=5)
    assert not entered.acquire(timeout=0.2)                # the third is waiting
    release.set()
    assert entered.acquire(timeout=5)
    for t in threads:
        t.join(5)


def test_background_work_has_slots_of_its_own_and_rechecks_after_waiting_for_one():
    gov, clock, _ = governor()
    release, threads, entered, raised = held_calls(gov, "background", 3, feature="triage")
    assert entered.acquire(timeout=5) and entered.acquire(timeout=5)
    assert not entered.acquire(timeout=0.2)                # the third waits for a background slot
    with gov.call("interactive", "ask", "m", MODEL):        # a click doesn't wait for either
        pass
    release.set()
    for t in threads:
        t.join(5)
    assert [(type(e), e.reason) for e in raised] == [(Deferred, "yielding")]   # someone clicked meanwhile


# ── yielding and presence ────────────────────────────────────────────────────

def test_background_and_speculative_work_yield_during_an_interactive_call_and_45s_after():
    gov, clock, _ = governor()
    with gov.call("interactive", "ask", "m", MODEL):
        assert gov.held_back("background", "triage", "m", MODEL) == "yielding"
        clock.shift(600)                                   # however long it runs
        assert gov.held_back("speculative", "draft-ahead", "m", MODEL) == "yielding"
    clock.shift(YIELD_SECONDS - 1)
    with pytest.raises(Deferred) as e:
        with gov.call("background", "triage", "m", MODEL):
            pytest.fail("triage went while the user was busy")
    assert e.value.reason == "yielding"
    clock.shift(1)
    assert gov.held_back("background", "triage", "m", MODEL) is None
    assert gov.held_back("speculative", "draft-ahead", "m", MODEL) is None


def test_an_interactive_call_is_never_held_back_by_another():
    gov, _, _ = governor()
    with gov.call("interactive", "ask", "m", MODEL):
        assert gov.held_back("interactive", "rewrite", "m", MODEL) is None


def test_speculative_work_needs_someone_here_in_the_last_45_minutes_and_background_does_not():
    gov, clock, _ = governor()
    clock.shift(PRESENCE_SECONDS)                          # starting up counted as someone here
    assert gov.held_back("speculative", "draft-ahead", "m", MODEL) is None
    clock.shift(1)
    assert gov.held_back("speculative", "draft-ahead", "m", MODEL) == "away"
    assert gov.held_back("background", "triage", "m", MODEL) is None
    gov.saw_someone()
    assert gov.held_back("speculative", "draft-ahead", "m", MODEL) is None


def test_scheduled_work_asks_for_someone_in_the_last_two_days():
    gov, clock, _ = governor()
    clock.shift(SCHEDULED_PRESENCE_SECONDS)
    assert gov.held_back("scheduled", "brief-ahead", "m", MODEL) is None
    clock.shift(1)
    assert gov.held_back("scheduled", "brief-ahead", "m", MODEL) == "away"
    gov.saw_someone()
    assert gov.held_back("scheduled", "brief-ahead", "m", MODEL) is None


def test_a_scheduled_call_yields_to_a_click_and_stops_at_the_limit():
    gov, clock, _ = governor(spent=10_000)
    assert gov.held_back("scheduled", "brief-ahead", "m", MODEL) == "limit"
    gov, clock, _ = governor()
    with gov.call("interactive", "ask", "m", MODEL):
        assert gov.held_back("scheduled", "brief-ahead", "m", MODEL) == "yielding"


def test_the_morning_brief_is_scheduled_work_and_a_brief_someone_opens_is_not():
    assert (features.kind("brief-ahead"), features.kind("brief")) == ("scheduled", "interactive")


# ── the daily limit ──────────────────────────────────────────────────────────

def test_at_the_limit_background_work_stops_and_an_interactive_call_needs_leave():
    gov, _, box = governor(spent=9_999, limit=10_000)
    assert gov.held_back("background", "triage", "m", MODEL) is None
    box["spent"] = 10_000
    assert gov.held_back("background", "triage", "m", MODEL) == "limit"
    assert gov.held_back("speculative", "draft-ahead", "m", MODEL) == "limit"
    with pytest.raises(OverLimit) as e:
        with gov.call("interactive", "ask", "m", MODEL):
            pytest.fail("an interactive call went over the limit unasked")
    assert (e.value.spent_micros, e.value.limit_micros) == (10_000, 10_000)
    went = []
    with allowing_over():
        with gov.call("interactive", "ask", "m", MODEL):
            went.append(1)
    with allowing_over(False), pytest.raises(OverLimit):
        with gov.call("interactive", "ask", "m", MODEL):
            pass
    assert went == [1]


def test_a_ledger_that_cant_be_read_stops_background_work_but_not_a_click(capsys):
    import sqlite3
    gov, _, _ = governor(spent=sqlite3.OperationalError("database is locked"))
    assert gov.held_back("background", "triage", "m", MODEL) == "limit"
    with gov.call("interactive", "ask", "m", MODEL):
        pass
    assert "the usage ledger can't be read: OperationalError" in capsys.readouterr().out


# ── the breaker ──────────────────────────────────────────────────────────────

def test_a_refused_key_stops_every_background_call_until_the_model_changes(capsys):
    gov, clock, _ = governor()
    gov.heard("triage", "m", MODEL, ModelError("Claude API 401: invalid x-api-key", 401))
    assert gov.held_back("background", "triage", "m", MODEL) == "breaker"
    assert gov.held_back("speculative", "draft-ahead", "other-model", MODEL) == "breaker"
    with gov.call("interactive", "ask", "m", MODEL):     # the user still sees the error
        pass
    clock.shift(YIELD_SECONDS)
    assert gov.snapshot()["breaker"] == ["every feature"]
    assert gov.held_back("background", "triage", "m", Model()) is None   # a new key made a new model
    assert gov.held_back("background", "triage", "m", MODEL) is None     # and the breaker forgot the old
    assert capsys.readouterr().out.count("keeps rejecting") == 1


def test_three_rejections_in_a_row_stop_that_feature_on_that_model_only():
    gov, _, _ = governor()
    bad = ModelError("Claude API 400: bad request", 400)
    for _ in range(TRIP_AFTER - 1):
        gov.heard("triage", "m", MODEL, bad)
    assert gov.held_back("background", "triage", "m", MODEL) is None
    gov.heard("triage", "m", MODEL, bad)
    assert gov.held_back("background", "triage", "m", MODEL) == "breaker"
    assert gov.held_back("background", "triage", "other-model", MODEL) is None
    assert gov.held_back("speculative", "draft-ahead", "m", MODEL) is None
    assert gov.snapshot()["breaker"] == ["triage on m"]


def test_a_reply_resets_the_count_and_a_failure_worth_retrying_never_counts():
    gov, _, _ = governor()
    bad = ModelError("Claude API 400: bad request", 400)
    for error in (bad, bad, None, bad, bad, ModelError("overloaded", 529), ModelError("no answer"), bad):
        gov.heard("triage", "m", MODEL, error)
    assert gov.held_back("background", "triage", "m", MODEL) == "breaker"   # only the last three in a row
    gov2, _, _ = governor()
    for error in (bad, bad, None, bad, bad):
        gov2.heard("triage", "m", MODEL, error)
    assert gov2.held_back("background", "triage", "m", MODEL) is None


def test_forget_is_a_fresh_start():
    gov, clock, _ = governor()
    gov.heard("triage", "m", MODEL, ModelError("Claude API 403: forbidden", 403))
    with gov.call("interactive", "ask", "m", MODEL):
        pass
    clock.shift(PRESENCE_SECONDS + 1)
    gov.forget()
    assert gov.held_back("background", "triage", "m", MODEL) is None
    assert gov.held_back("speculative", "draft-ahead", "m", MODEL) is None
    assert gov.snapshot() == {"spent_today_micros": 0, "limit_micros": 10_000, "reserved_micros": 0, "interactive_running": 0,
                              "since_interactive": None, "since_seen": 0.0, "breaker": []}


# ── the limit in the config ──────────────────────────────────────────────────

@pytest.mark.parametrize("value, limit", [
    (2.5, 2.5), (40, 40.0), ("7.5", 7.5),
    (0, 15.0), (-3, 15.0), ("lots", 15.0), (None, 15.0), (float("nan"), 15.0), (float("inf"), 15.0),
    ("missing", 15.0)])
def test_the_daily_limit_is_a_positive_number_of_dollars_or_15(data_dir, value, limit):
    from mailmind.bootstrap import assistant
    body = {"accounts": []} if value == "missing" else {"accounts": [], "ai_daily_limit_usd": value}
    (data_dir / "mailmind.config.json").write_text(json.dumps(body))
    assert assistant.limit_micros() == round(limit * 1_000_000)


def test_a_config_that_cant_be_parsed_leaves_the_limit_at_15(data_dir):
    from mailmind.bootstrap import assistant
    (data_dir / "mailmind.config.json").write_text("{ not json")
    assert assistant.limit_micros() == 15_000_000
