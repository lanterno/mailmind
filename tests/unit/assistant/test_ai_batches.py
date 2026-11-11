"""A backlog of triage through the Message Batches API: what each transport sends and reads, what a batch
costs and how that counts against the daily limit, and the job that sends one and brings its answers in.
Everything here is against the fake API; nothing real is sent."""

import pytest

from fakes import anthropic_api as fa
from mailmind.assistant.domain.prompts import SYSTEM

TRANSPORTS = ["sdk", "urllib"]
SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}


@pytest.fixture(params=TRANSPORTS)
def transport(request, data_dir, monkeypatch):
    from mailmind.bootstrap import assistant
    monkeypatch.setattr(assistant.status, "_last", {"ok": None, "ts": 0, "error": ""})
    monkeypatch.setattr(assistant.calls, "MODEL", None)
    monkeypatch.delenv("MAILMIND_NO_SCHEMA", raising=False)
    monkeypatch.delenv("MAILMIND_MODEL", raising=False)
    assistant.use(request.param)
    yield request.param
    assistant.use("urllib")


@pytest.fixture
def gw(transport, ai):
    from mailmind.bootstrap import assistant
    return assistant.gateway()


def posts(ai):
    return [r for r in ai.requests if r.method == "POST" and r.path.startswith(fa.BATCHES)]


# -- what each transport sends and reads ---------------------------------------

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
