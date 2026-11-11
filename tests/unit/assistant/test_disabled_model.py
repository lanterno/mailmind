"""The model with no API key (mailmind.assistant.adapters.disabled): AI is off, so every call, a batch's steps among them,
raises AIOff unsent. A key removed between a batch round's check and its call lands here, and AIOff is a NotSent, which
every caller lets through: nothing is counted as a call that failed."""
import pytest

from mailmind.assistant.adapters.disabled import DisabledModel
from mailmind.assistant.application.gateway import Gateway
from mailmind.assistant.application.ports import AIOff, NotSent, Prompt


class _Ledger:
    def __init__(self):
        self.recorded = []

    def record(self, spend):
        self.recorded.append(spend)


PROMPT = Prompt(text="Triage this.", model="claude-opus-5-5", max_tokens=100)

STEPS = {
    "complete": lambda m: m.complete(PROMPT),
    "submit_batch": lambda m: m.submit_batch([("1", PROMPT)]),
    "batch_ended": lambda m: m.batch_ended("msgbatch_1"),
    "batch_results": lambda m: m.batch_results("msgbatch_1", "claude-opus-5-5"),
    "discard_batch": lambda m: m.discard_batch("msgbatch_1"),
}

THROUGH_THE_GATEWAY = {
    "ask": lambda g: g.ask("Triage this.", feature="triage"),
    "submit_batch": lambda g: g.submit_batch([("1", "Triage this.")], feature="triage_batch"),
    "batch_ended": lambda g: g.batch_ended("msgbatch_1"),
    "batch_results": lambda g: g.batch_results("msgbatch_1", feature="triage_batch"),
    "discard_batch": lambda g: g.discard_batch("msgbatch_1"),
}


def test_it_says_ai_is_off():
    assert DisabledModel().enabled is False


@pytest.mark.parametrize("step", STEPS.values(), ids=STEPS.keys())
def test_each_call_raises_ai_off(step):
    with pytest.raises(AIOff, match="there is no API key"):
        step(DisabledModel())


@pytest.mark.parametrize("step", THROUGH_THE_GATEWAY.values(), ids=THROUGH_THE_GATEWAY.keys())
def test_through_the_gateway_it_is_not_sent_and_nothing_is_counted(step):
    ledger = _Ledger()
    with pytest.raises(NotSent):
        step(Gateway(DisabledModel, ledger))
    assert ledger.recorded == []
