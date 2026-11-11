"""A backlog of triage through the Message Batches API: what the SDK's adapter sends and reads, what a batch
costs and how that counts against the daily limit, and the job that sends one and brings its answers in.
Everything here is against the fake API; nothing real is sent."""
import time

import pytest

from assistant_ports import Model, Triage, message, triage_on
from fakes import anthropic_api as fa
from mailmind.assistant.application.governor import CostGovernor
from mailmind.assistant.application.ports import ModelError
from mailmind.assistant.domain import models, pricing
from mailmind.assistant.domain.prompts import SYSTEM

SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}


@pytest.fixture
def plain(data_dir, monkeypatch):
    from mailmind.bootstrap import assistant
    monkeypatch.setattr(assistant.status, "_last", {"ok": None, "ts": 0, "error": ""})
    monkeypatch.setattr(assistant.calls, "MODEL", None)
    monkeypatch.delenv("MAILMIND_NO_SCHEMA", raising=False)
    monkeypatch.delenv("MAILMIND_MODEL", raising=False)


@pytest.fixture
def gw(plain, ai):
    from mailmind.bootstrap import assistant
    return assistant.gateway()


def posts(ai):
    return [r for r in ai.requests if r.method == "POST" and r.path.startswith(fa.BATCHES)]


# -- what the adapter sends and reads -------------------------------------------

def test_a_batch_carries_each_request_and_never_the_fallback(gw, ai):
    batch_id = gw.submit_batch([("11", "first"), ("12", "second")], SCHEMA, feature="triage-batch", effort="low")
    assert batch_id in ai.batches
    sent = ai.batches[batch_id]["requests"]
    assert [r["custom_id"] for r in sent] == ["11", "12"]
    p = sent[0]["params"]
    assert p["messages"] == [{"role": "user", "content": "first"}]
    assert p["system"] == sent[1]["params"]["system"] == SYSTEM
    assert p["output_config"]["effort"] == "low" and p["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert "fallbacks" not in p and "fallback" not in str(posts(ai)[0].headers.get("anthropic-beta", ""))
    assert posts(ai)[0].key == ai.KEY


def test_sending_a_batch_is_never_retried(gw, ai):
    ai.queue(fa.error(500, "api_error", "try later"), fa.error(500, "api_error", "try later"))
    with pytest.raises(RuntimeError):
        gw.submit_batch([("1", "x")], feature="triage-batch")
    assert len(posts(ai)) == 1          # no key says "the same batch": another try could pay for it twice


def test_reading_a_batch_says_when_it_has_ended(gw, ai):
    batch_id = gw.submit_batch([("1", "x")], feature="triage-batch")
    assert gw.batch_ended(batch_id) is False
    ai.end_batch(batch_id)
    assert gw.batch_ended(batch_id) is True


def test_each_way_a_request_can_end_is_read(gw, ai):
    batch_id = gw.submit_batch([(str(i), "x") for i in range(1, 7)], feature="triage-batch")
    ai.end_batch(batch_id, {"1": fa.succeeded({"summary": "fine"}), "2": fa.errored("invalid_request_error"),
                            "3": fa.errored("overloaded_error"), "4": fa.EXPIRED, "5": fa.CANCELED,
                            "6": {"type": "succeeded", "message": fa.answer("", stop="refusal")[1]}})
    got = {r.custom_id: r for r in gw.batch_results(batch_id, feature="triage-batch")}
    assert got["1"].reply.text == '{"summary": "fine"}'
    assert (got["2"].reply, got["2"].permanent) == (None, True)
    for i in "345":
        assert (got[i].reply, got[i].permanent) == (None, False)
    assert got["6"].reply.refused and got["6"].reply.text == ""


def test_a_batch_can_be_removed_and_a_gone_one_answers_404(gw, ai):
    from mailmind.assistant.application.ports import ModelError
    batch_id = gw.submit_batch([("1", "x")], feature="triage-batch")
    ai.end_batch(batch_id)
    gw.discard_batch(batch_id)
    assert ai.deleted == [batch_id]
    with pytest.raises(ModelError) as e:
        gw.batch_ended(batch_id)
    assert e.value.status == 404


# -- what it costs ---------------------------------------------------------------

def test_a_batch_costs_half_what_the_same_calls_do(gw, ai):
    from mailmind.assistant.domain import pricing
    batch_id = gw.submit_batch([("1", "x"), ("2", "y")], feature="triage-batch")
    ai.end_batch(batch_id, {k: fa.succeeded({"summary": "s"}, input_tokens=1000, output_tokens=100) for k in "12"})
    gw.batch_results(batch_id, feature="triage-batch")
    full = pricing.cost_micros("claude-opus-5-5", "claude-opus-5-5", 1000, 100)
    today = gw.usage(1)[0]["features"]["triage-batch"]
    assert today == {"calls": 2, "cost_micros": 2 * pricing.batch_micros(full)}
    assert pricing.batch_micros(full) * 2 in (full, full + 1)


def test_half_a_micro_dollar_rounds_up():
    from mailmind.assistant.domain import pricing
    assert (pricing.batch_micros(3), pricing.batch_micros(4), pricing.batch_micros(0)) == (2, 2, 0)


def test_a_request_with_no_answer_costs_nothing_but_is_counted(gw, ai):
    batch_id = gw.submit_batch([("1", "x"), ("2", "y")], feature="triage-batch")
    ai.end_batch(batch_id, {"1": fa.EXPIRED, "2": fa.errored("invalid_request_error")})
    gw.batch_results(batch_id, feature="triage-batch")
    assert gw.usage(1)[0]["features"]["triage-batch"] == {"calls": 2, "cost_micros": 0}


def test_requests_a_batch_keeps_rejecting_trip_the_breaker(gw, ai):
    batch_id = gw.submit_batch([(str(i), "x") for i in range(3)], feature="triage-batch")
    ai.end_batch(batch_id, {str(i): fa.errored("invalid_request_error") for i in range(3)})
    gw.batch_results(batch_id, feature="triage-batch")
    assert gw.held_back("triage-batch") == "breaker"
    assert gw.held_back("triage") is None       # the ordinary path goes on


def test_an_estimate_errs_high():
    from mailmind.assistant.domain import pricing
    chars = 3000                                # about 750 tokens of mail, and an answer of about 100
    assert pricing.batch_estimate("claude-opus-5-5", chars) > pricing.batch_micros(
        pricing.cost_micros("claude-opus-5-5", "claude-opus-5-5", 750, 100))


# -- the job, on stored mail and a model of its own --------------------------------

class Backlog:
    """Eight messages waiting for triage, a backlog from 5 up with the 2 newest kept back, asked about at every round."""

    def __init__(self, model=None, governor=None, ledger=None):
        self.model, self.triage = model or Model(), Triage(message(n) for n in range(1, 9))
        self.job = triage_on(self.model, self.triage, ledger, governor)
        self.job.BATCH_MIN, self.job.BATCH_KEEP_NEWEST, self.job.BATCH_POLL = 5, 2, 0

    def waiting(self):
        return [r["message_key"] for r in self.triage.needs_summary(None, 1000)]

    def sent(self):
        return [int(cid) for cid in self.model.batched]

    def states(self):
        return [b["state"] for b in self.triage.batches.values()]

    def estimates(self, rows):
        return [pricing.batch_estimate(models.DEFAULT, len(SYSTEM) + len(self.job._triage_prompt(r))) for r in rows]


@pytest.fixture
def backlog():
    return Backlog()


def test_a_backlog_goes_as_a_batch_and_the_newest_stay(backlog):
    before = backlog.waiting()
    assert backlog.job.batch_submit() == len(before) - 2
    assert backlog.sent() == before[2:]
    assert backlog.waiting() == before[:2]


def test_a_few_new_messages_are_no_backlog(backlog):
    backlog.job.BATCH_MIN = len(backlog.waiting()) + 1
    assert backlog.job.batch_submit() == 0
    assert backlog.model.asked == []


def test_one_batch_at_a_time(backlog):
    assert backlog.job.batch_submit() > 0
    assert backlog.job.batch_submit() == 0
    assert backlog.model.asked == ["submit_batch"]


def test_a_batch_is_no_bigger_than_its_maximum(backlog):
    backlog.job.BATCH_MAX = 3
    assert backlog.job.batch_submit() == 3


def test_an_answer_for_nothing_of_ours_is_ignored(backlog):
    ours = backlog.job.batch_submit()
    [batch] = backlog.model.batches.values()
    prompt = batch["requests"][0][1]
    batch["requests"] += [("not-a-key", prompt), ("999999", prompt)]
    backlog.model.end_batch()
    assert backlog.job.batch_collect() == ours


def test_nothing_is_brought_in_before_the_batch_ends(backlog):
    assert backlog.job.batch_submit() > 0
    assert backlog.job.batch_collect() == 0
    assert backlog.job.batch_status()["waiting"] > 0
    assert backlog.model.discarded == []


def test_it_asks_only_so_often(backlog):
    backlog.job.BATCH_POLL = 300
    assert backlog.job.batch_submit() > 0
    backlog.model.asked.clear()
    backlog.job.batch_collect()
    assert backlog.model.asked == []
    backlog.triage.batch_open(None)["polled_at"] -= 301
    backlog.job.batch_collect()
    assert backlog.model.asked == ["batch_ended"]
    backlog.job.batch_collect()
    assert backlog.model.asked == ["batch_ended"]


def test_a_batch_is_removed_from_the_api_once_its_answers_are_stored(backlog):
    assert backlog.job.batch_submit() > 0
    [batch] = backlog.model.batches
    backlog.model.end_batch()
    backlog.job.batch_collect()
    assert (backlog.model.discarded, backlog.states()) == ([batch], ["done"])


def test_removing_it_is_tried_again_when_it_fails(backlog):
    assert backlog.job.batch_submit() > 0
    [batch] = backlog.model.batches
    backlog.model.end_batch()
    backlog.model.failing["discard_batch"] = ModelError("no", 400)
    assert backlog.job.batch_collect() > 0
    assert (backlog.model.discarded, backlog.states()) == ([], ["collected"])
    del backlog.model.failing["discard_batch"]
    assert backlog.job.batch_collect() == 0                 # nothing is stored twice
    assert (backlog.model.discarded, backlog.states()) == ([batch], ["done"])


def test_a_batch_the_api_no_longer_has_gives_its_messages_back(backlog):
    assert backlog.job.batch_submit() > 0
    backlog.model.batches.clear()
    backlog.job.batch_collect()
    assert (backlog.triage.batches, backlog.job.batch_status()["waiting"]) == ({}, 0)


def test_an_outage_leaves_the_batch_out(backlog):
    assert backlog.job.batch_submit() > 0
    backlog.model.failing["batch_ended"] = ModelError("busy", 503)
    assert backlog.job.batch_collect() == 0
    assert backlog.job.batch_status()["state"] == "running"
    del backlog.model.failing["batch_ended"]
    backlog.model.end_batch()
    assert backlog.job.batch_collect() > 0


def test_a_crash_while_sending_gives_its_messages_back_in_time(backlog):
    lately = int(time.time()) - backlog.job.BATCH_SEND_GIVE_UP + 60       # begun, and not sent, a little under the time ago
    backlog.triage.batch_begin(None, "triage-batch", models.DEFAULT, backlog.waiting()[:2], 10, 0, lately)
    backlog.job.batch_collect()
    assert backlog.job.batch_status()["waiting"] == 2     # it may still be going
    assert backlog.job.batch_submit() == 0
    backlog.triage.batch_open(None)["created_at"] -= 61
    backlog.job.batch_collect()
    assert backlog.job.batch_status()["waiting"] == 0


# -- within the daily limit ------------------------------------------------------

def limited(micros, spent=0):
    """A backlog whose governor allows `micros` a day, `spent` of it gone."""
    return Backlog(governor=CostGovernor(spent=lambda: spent, limit=lambda: micros))


def test_a_batch_takes_only_its_share_of_what_the_limit_leaves():
    each = Backlog().estimates([message(n) for n in range(6, 0, -1)])     # what the six oldest ask, newest first
    backlog = limited((sum(each[:3]) + each[3] // 2) * Backlog().job.BATCH_SHARE)
    assert backlog.job.batch_submit() == 3


def test_what_a_batch_books_counts_the_system_prompt_each_request_carries(backlog):
    rows = backlog.triage.needs_summary(None, 1000)[2:]
    assert backlog.job.batch_submit(at_least=1) == len(rows) > 0
    assert backlog.job.batch_status()["estimate_micros"] == sum(backlog.estimates(rows))


def test_at_the_limit_no_batch_goes():
    backlog = limited(15_000_000, spent=15_000_000)
    assert backlog.job.batch_submit() == 0
    assert backlog.model.asked == []


def test_a_limit_with_no_room_for_one_request_sends_nothing():
    backlog = limited(1)
    assert backlog.job.batch_submit() == 0
    assert backlog.model.asked == []


@pytest.mark.parametrize("held", ["yielding", "limit"])
def test_answers_are_brought_in_at_the_limit_and_while_someone_uses_ai(held):
    """They are paid for already, and bringing them in sends no mail."""
    model, today = Model(), {"spent": 0}
    governor = CostGovernor(spent=lambda: today["spent"], limit=lambda: 15_000_000)
    backlog = Backlog(model, governor)
    assert backlog.job.batch_submit() > 0
    model.end_batch()
    if held == "yielding":
        with governor.call("interactive", "ask", models.DEFAULT, model):
            pass                                            # someone asked just now: the rest yields
    else:
        today["spent"] = 15_000_000
    assert backlog.job.round() == (backlog.job.ASSESS_YIELD if held == "yielding" else None)
    assert backlog.job.batch_status()["waiting"] == 0
    assert model.discarded and model.sent == []


# -- the round, and AI off -------------------------------------------------------

def test_the_round_sends_the_backlog_and_triages_the_newest_at_once(backlog):
    before = backlog.waiting()
    backlog.job.round()
    assert backlog.model.asked == ["submit_batch"]
    assert len(backlog.model.sent) == 2
    assert backlog.waiting() == [] and len(backlog.sent()) == len(before) - 2


def test_the_switch_keeps_the_round_from_sending_one_and_still_brings_one_in(backlog, monkeypatch):
    assert backlog.job.batch_submit() > 0
    backlog.model.end_batch()
    for n in range(9, 15):
        backlog.triage.add(message(n))                      # a backlog again
    monkeypatch.setenv("MAILMIND_NO_BATCHES", "1")
    backlog.job.round()
    assert backlog.model.discarded                          # the one that was out came in
    assert backlog.model.asked.count("submit_batch") == 1   # and no other went, though mail is waiting


def test_with_ai_off_nothing_goes_and_nothing_is_touched():
    off = Model(enabled=False)
    backlog = Backlog(off)
    assert backlog.job.batch_submit(at_least=1, keep=0) == 0
    assert backlog.triage.batches == {}
    triage = backlog.triage
    triage.batch_sent(None, triage.batch_begin(None, "triage-batch", models.DEFAULT, backlog.waiting()[:2], 10, 0, 0),
                      "msgbatch_x", 0)
    assert backlog.job.batch_collect() == 0
    assert (off.asked, off.sent, backlog.job.batch_status()["waiting"]) == ([], [], 2)


def test_while_someone_uses_ai_no_batch_goes_and_the_backlog_still_waits():
    """A backlog yields to a person as every background call does: nothing is asked of the model, and none of it is
    marked as sent, so the next round finds it all still waiting."""
    model = Model()
    governor = CostGovernor(spent=lambda: 0, limit=lambda: 15_000_000)
    backlog = Backlog(model, governor)
    before = backlog.waiting()
    with governor.call("interactive", "ask", models.DEFAULT, model):
        pass                                                # someone asked just now
    assert backlog.job.batch_submit() == 0
    assert model.asked == []
    assert backlog.waiting() == before and backlog.job.batch_status()["waiting"] == 0
