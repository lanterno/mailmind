"""Why a sender is worth cleaning up: the numbers are the mail's, and what comes back is the model's own words on them, held to what it was shown."""
import pytest

from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.assistant.domain import rationale
from mailmind.bootstrap import assistant
from mailmind.bootstrap.curation import rules

LOOM, THIMBLE = "news@loom.example.test", "orders@thimble.example.test"
NEWSLETTER = "ada-inbox-03-newsletter.eml"
NOW = 1_000_000_000.0


def row(addr, n=47, unseen=47, replied=0, first_days=180, last_days=2):
    return {"addr": addr, "name": "Loom", "n": n, "unseen": unseen, "replied": replied, "first_ts": NOW - first_days * 86400, "last_ts": NOW - last_days * 86400}


# ── who may be asked about ───────────────────────────────────────────────────────────────────────

def test_a_list_names_each_sender_once_in_lower_case():
    assert rationale.named([" News@Loom.Example.test ", LOOM, THIMBLE]) == [LOOM, THIMBLE]


@pytest.mark.parametrize("raw", [None, "x@y.test", [], ["nobody"], [LOOM, 5], ["a" * 400 + "@example.test"],
                                 [f"a{i}@example.test" for i in range(rationale.MAX_SENDERS + 1)]])
def test_a_list_that_wont_read_names_nobody(raw):
    assert rationale.named(raw) is None


# ── what is taken from an answer ─────────────────────────────────────────────────────────────────

def test_the_model_is_told_the_numbers_and_only_those():
    text = rationale.line(1, row(LOOM), NOW)
    assert text == "[1] Loom <news@loom.example.test>: 47 messages over 6 months, 47 of them never opened, 0 replied to, the latest 2 days ago."


@pytest.mark.parametrize("days, words", [(0, "under a day"), (1, "1 day"), (59, "59 days"), (60, "2 months"), (800, "2 years")])
def test_how_long_reads_in_words(days, words):
    assert rationale.span(days * 86400) == words


def test_a_rationale_stands_for_the_sender_it_numbers():
    rows = [row(LOOM), row(THIMBLE)]
    said = rationale.clean({"rationales": [{"id": 2, "text": " Never opened. "}]}, rows, NOW)
    assert said[THIMBLE] == "Never opened."


def test_a_sender_the_model_passed_over_gets_the_numbers_in_plain_words():
    said = rationale.clean({"rationales": []}, [row(LOOM)], NOW)
    assert said == {LOOM: "47 of 47 unopened over 6 months, 0 replied to."}


@pytest.mark.parametrize("n", [0, 2, None, "1", True])
def test_a_number_it_wasnt_shown_says_nothing_of_anyone(n):
    said = rationale.clean({"rationales": [{"id": n, "text": "made up"}]}, [row(LOOM)], NOW)
    assert "made up" not in said[LOOM]


@pytest.mark.parametrize("told", [{}, {"rationales": "text"}, {"rationales": [1, "x", None]}])
def test_an_answer_that_wont_read_falls_back(told):
    assert rationale.clean(told, [row(LOOM)], NOW)[LOOM].startswith("47 of 47")


def test_a_rationale_is_held_to_its_length():
    said = rationale.clean({"rationales": [{"id": 1, "text": "x" * 900}]}, [row(LOOM)], NOW)
    assert len(said[LOOM]) == rationale.TEXT_MAX


# ── the route ────────────────────────────────────────────────────────────────────────────────────

def test_the_rationale_is_written_for_each_sender_with_mail(app, world, ai):
    ai.queue(fa.answer({"rationales": [{"id": 1, "text": "You have never opened these."}]}))
    r = app.post("/api/cleanup/rationale", {"senders": [LOOM, "nobody@example.test"]})
    assert (r.status, r.json["ok"], r.json["rationales"]) == (200, True, {LOOM: "You have never opened these."})
    assert LOOM in ai.requests[0].prompt and "1 messages over" in ai.requests[0].prompt


def test_each_message_counts_once_however_many_folders_hold_it(app, world, ai):
    world.deliver(w.ADA, "Archive", w.variant(NEWSLETTER), (w.SEEN,))
    app.post("/api/cleanup/rationale", {"senders": [LOOM]})
    assert "1 messages over" in ai.requests[0].prompt
    assert ", 0 of them never opened" in ai.requests[0].prompt      # read in one of its copies is read


def test_it_changes_nothing_and_is_counted_under_its_own_feature(app, world, ai):
    before, mark = rules.load(), world.mark()
    app.post("/api/cleanup/rationale", {"senders": [LOOM]})
    assert (rules.load(), world.writes(mark)) == (before, [])
    assert assistant.calls.usage(1)[0]["features"]["rationale"]["calls"] == 1


def test_with_no_mail_from_any_of_them_nothing_is_sent(app, world, ai):
    r = app.post("/api/cleanup/rationale", {"senders": ["nobody@example.test"]})
    assert (r.status, r.json["rationales"], ai.requests) == (200, {}, [])


def test_a_list_that_wont_read_is_the_400(app, world, ai):
    r = app.post("/api/cleanup/rationale", {"senders": ["nobody"]})
    assert (r.status, r.json["detail"], ai.requests) == (400, "bad senders", [])
