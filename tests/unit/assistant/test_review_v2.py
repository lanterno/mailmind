"""The week's review says what a person filed away, by the command log and true after an Undo, and what the invites in the mail
say of their week."""
from datetime import datetime, timedelta

import pytest

from fixtures import world as w
from mailmind.assistant.domain import review


@pytest.mark.parametrize("handled, says", [(3, True), (0, False)])
def test_with_no_answer_the_week_says_so_itself(handled, says):
    week = {"sent": 1, "received": 2, "by_bucket": {}, "needs_you": 0, "waiting_on": 0, "reply_later": 0, "cleaned": 0, "handled": handled}
    assert ("You filed away" in review.fallback(week, [], [])["narrative"]) is says


# ── meetings and deadlines ────────────────────────────────────────────────────────────────────────

NOON = datetime(2026, 11, 2, 12, 0)
SINCE, UNTIL = NOON - timedelta(days=7), NOON + timedelta(days=7)


def event(summary, when, all_day=False):
    return {"key": 1, "folder": "INBOX", "uid": 1, "account": w.ADA, "thread_id": "t", "summary": summary, "start": when.isoformat(timespec="minutes"),
            "all_day": all_day}


def of(gone=(), coming=()):
    return review.meetings_of(gone, coming, SINCE, NOON, UNTIL)


def test_an_event_past_and_one_to_come_are_told_apart():
    a, b = event("Before", NOON - timedelta(days=1)), event("After", NOON + timedelta(days=1))
    assert [(m["summary"], m["past"]) for m in of([a], [b])] == [("Before", True), ("After", False)]


def test_one_in_both_answers_is_listed_once():
    a = event("Standup", NOON - timedelta(days=1))
    assert len(of([a], [a])) == 1


@pytest.mark.parametrize("when, kept", [(SINCE, True), (SINCE - timedelta(minutes=1), False), (UNTIL - timedelta(minutes=1), True), (UNTIL, False)])
def test_the_week_is_its_bounds(when, kept):
    assert bool(of([event("Edge", when)])) is kept


def test_it_keeps_the_latest_gone_and_the_soonest_to_come():
    gone = [event(f"g{i}", NOON - timedelta(hours=i + 1)) for i in range(9)]
    coming = [event(f"c{i}", NOON + timedelta(hours=i + 1)) for i in range(9)]
    kept = of(gone, coming)
    assert [m["summary"] for m in kept] == [f"g{i}" for i in range(review.EVENTS_EACH_WAY - 1, -1, -1)] + [f"c{i}" for i in range(review.EVENTS_EACH_WAY)]


def test_a_long_title_is_held_to_its_cap():
    assert len(of([event("t" * 900, NOON - timedelta(hours=1))])[0]["summary"]) == review.EVENT_SUMMARY_MAX


def test_the_model_is_told_which_are_gone_and_which_are_deadlines():
    said = review.meeting_lines(of([event("Offsite", NOON - timedelta(days=1))], [event("Tax return", NOON + timedelta(days=1), all_day=True)]))
    assert said == "- 2026-11-01 12:00 (already happened): Offsite\n- 2026-11-03 (all day): Tax return"


def test_with_no_events_the_model_is_told_none():
    assert review.meeting_lines([]) == "(none)"
