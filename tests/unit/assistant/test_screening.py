"""The AI's pass over the Screener: only what it is sure of is said, it names only who it was shown, and it writes no rule."""
import pytest

from assistant_ports import Ledger, Model, Uow, calls_on
from mailmind.assistant.application.screening import Screening
from mailmind.assistant.domain import screening

ADDRS = ["orders@thimble.example.test", "lin@example.test"]


def said(n=1, verdict="keep", confidence=0.95, why="a shop you use", **more):
    return {"id": n, "verdict": verdict, "confidence": confidence, "why": why, **more}


# ── what is taken from an answer ─────────────────────────────────────────────────────────────────

def test_a_sure_verdict_stands_for_the_sender_it_numbers():
    assert screening.clean({"verdicts": [said(2, "block", 0.9, "bulk mail")]}, ADDRS) == [
        {"from_addr": "lin@example.test", "verdict": "block", "confidence": 0.9, "why": "bulk mail"}]


@pytest.mark.parametrize("confidence", [0.79, 0, -1, None, "0.95", True, float("nan")])
def test_what_isnt_sure_enough_says_nothing(confidence):
    assert screening.clean({"verdicts": [said(confidence=confidence)]}, ADDRS) == []


def test_exactly_the_bar_is_sure_enough():
    assert [s["confidence"] for s in screening.clean({"verdicts": [said(confidence=screening.MIN_CONFIDENCE)]}, ADDRS)] == [0.8]


def test_more_than_certain_reads_as_certain():
    assert [s["confidence"] for s in screening.clean({"verdicts": [said(confidence=7)]}, ADDRS)] == [1.0]


@pytest.mark.parametrize("n", [0, 3, -1, None, "1", True, 1.0])
def test_a_number_it_wasnt_shown_names_nobody(n):
    assert screening.clean({"verdicts": [said(n)]}, ADDRS) == []


@pytest.mark.parametrize("verdict", ["remove", "allow", "", None])
def test_a_verdict_that_is_no_rules_says_nothing(verdict):
    assert screening.clean({"verdicts": [said(verdict=verdict)]}, ADDRS) == []


def test_the_first_word_on_a_sender_stands():
    got = screening.clean({"verdicts": [said(1, "keep"), said(1, "block")]}, ADDRS)
    assert [s["verdict"] for s in got] == ["keep"]


@pytest.mark.parametrize("told", [{}, {"verdicts": "keep them all"}, {"verdicts": ["keep", 1, None]}])
def test_an_answer_that_wont_read_says_nothing(told):
    assert screening.clean(told, ADDRS) == []


def test_a_reason_is_held_to_its_length():
    got = screening.clean({"verdicts": [said(why="x" * 500)]}, ADDRS)
    assert len(got[0]["why"]) == screening.WHY_MAX


def test_a_senders_line_is_held_to_its_length():
    row = {"subject": "s" * 2000, "summary": None}
    text = screening.line(1, {"from_name": "N", "from_addr": ADDRS[0], "count": 1}, [row])
    assert len(text) <= 400 and text.startswith("[1] N <orders@thimble.example.test>")


# ── the pass, on fakes behind its ports ──────────────────────────────────────────────────────────

class Mail:
    """The senders waiting in the Screener, and what is stored of each."""

    def __init__(self, n=len(ADDRS)):
        self.waiting = [{"from_addr": f"s{i}@example.test", "from_name": f"S{i}", "count": 1} for i in range(n)]

    def screener_items(self, account):
        return self.waiting

    def sender_recent(self, c, addr, n):
        return [{"subject": "Your order", "summary": None}]


def suggest(model, ledger=None, mail=None):
    return Screening(calls_on(model, ledger), Uow(), mail or Mail()).suggest("ada@example.test")


def test_a_pass_is_counted_under_its_own_feature():
    ledger = Ledger()
    suggest(Model({"verdicts": []}), ledger)
    assert ledger.features == ["screen"]


def test_a_pass_reads_no_more_than_its_senders():
    model = Model({"verdicts": []})
    assert suggest(model, mail=Mail(screening.MAX_SENDERS + 10))["asked"] == screening.MAX_SENDERS
    assert f"[{screening.MAX_SENDERS}] " in model.prompt and f"[{screening.MAX_SENDERS + 1}]" not in model.prompt
