"""The week's review, written ahead on Monday morning and read all week: on Monday morning only, once triage has read the last
day's mail, once a week, within the cost governor, and which stored review a read then finds. Reviews on a week of its own, a
model of its own and the real governor, on a clock that starts as the app does, on Sunday at noon. The last test is the table's:
it keeps a review written on Monday morning past the end of its week."""
from datetime import datetime, timedelta, timezone

import pytest
import time_machine

from assistant_ports import Ledger, Model, Said, Uow, Week, calls_on
from mailmind.assistant.application.governor import CostGovernor
from mailmind.assistant.application.ports import ModelError
from mailmind.assistant.application.review import Reviews
from mailmind.assistant.domain import features

ADA = "ada@example.test"
STARTED = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)      # Sunday, when the app started, with someone there
MONDAY = datetime(2026, 11, 9, 7, 0, tzinfo=timezone.utc)
SUNDAY_NIGHT = datetime(2026, 11, 15, 23, 59, tzinfo=timezone.utc)
AHEAD = "A steady week: Ivo still wants an answer on the contract, and the rest is done."
LATER = "Midweek: the contract is signed, and only the invoice is left to answer."


class Ahead:
    """Reviews, and what it was told and kept, built at STARTED and then moved to Monday morning."""

    def __init__(self, clock):
        self.clock, self.model, self.ledger, self.said, self.week = clock, Model(), Ledger(), Said(), Week()
        self.governor = CostGovernor(spent=lambda: 0, limit=lambda: 15_000_000)
        self.reviews = Reviews(calls_on(self.model, self.ledger, self.governor), self.said, Uow(), self.week)
        clock.move_to(MONDAY)

    def round(self):
        self.reviews.write_ahead({ADA})

    def written(self):
        return [r["narrative"] for r in self.said.reviews.values()]

    def opened(self, week=True):
        """What Your week shows, as a GET reads it: what is stored, and no call."""
        return self.reviews.build({ADA}, generate=False, week=week)


@pytest.fixture
def ahead():
    with time_machine.travel(STARTED, tick=False) as clock:
        yield Ahead(clock)


def not_triaged(ahead, age):
    """A message triage hasn't read yet, `age` before Monday morning."""
    ahead.said.waiting.append({"pk": 9, "ts": int((MONDAY - age).timestamp())})


# ── when it is written ────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("hour", [5, 11])
def test_monday_morning_is_from_five_until_noon_and_counted_as_written_ahead(ahead, hour):
    ahead.model.answer({"narrative": AHEAD, "next": []})
    ahead.clock.move_to(MONDAY.replace(hour=hour))
    ahead.round()
    assert (ahead.written(), ahead.ledger.features) == ([AHEAD], ["review-ahead"])


@pytest.mark.parametrize("hour", [0, 4, 12, 23])
def test_outside_monday_morning_nothing_is_written(ahead, hour):
    ahead.clock.move_to(MONDAY.replace(hour=hour))
    ahead.round()
    assert (ahead.model.sent, ahead.written()) == ([], [])


@pytest.mark.parametrize("days", range(1, 7))
def test_on_any_other_morning_of_the_week_nothing_is_written(ahead, days):
    ahead.clock.move_to(MONDAY + timedelta(days=days))
    ahead.round()
    assert (ahead.model.sent, ahead.written()) == ([], [])


def test_the_review_written_this_week_is_left_alone(ahead):
    ahead.round()
    ahead.round()
    assert len(ahead.model.sent) == 1


def test_one_opened_earlier_on_monday_is_the_weeks_and_none_is_written(ahead):
    ahead.clock.move_to(MONDAY.replace(hour=4))
    ahead.reviews.build({ADA}, week=True)                 # someone opened Your week before the morning
    ahead.clock.move_to(MONDAY)
    ahead.round()
    assert (len(ahead.model.sent), ahead.ledger.features) == (1, ["review"])


def test_it_waits_for_triage_to_read_the_last_days_mail(ahead):
    not_triaged(ahead, timedelta(hours=2))
    ahead.round()
    assert ahead.model.sent == []


def test_an_older_backlog_doesnt_hold_it_up(ahead):
    not_triaged(ahead, timedelta(days=3))
    ahead.round()
    assert len(ahead.model.sent) == 1


def test_after_days_away_nothing_is_written_until_someone_is_back(ahead):
    ahead.clock.move_to(MONDAY + timedelta(days=7))        # nobody has been here since the Sunday before last
    ahead.round()
    assert (ahead.model.sent, ahead.written()) == ([], [])
    ahead.governor.saw_someone()
    ahead.round()
    assert len(ahead.written()) == 1


def test_a_failed_call_waits_for_the_next_round(ahead):
    ahead.model.answer(ModelError("bad request", 400))
    ahead.round()
    assert ahead.written() == []
    ahead.round()
    assert len(ahead.written()) == 1


def test_it_is_scheduled_work_and_a_review_someone_opens_is_not():
    assert (features.kind("review-ahead"), features.kind("review")) == ("scheduled", "interactive")


# ── what a read finds ─────────────────────────────────────────────────────────────────────────────

def test_the_one_written_ahead_is_read_all_week_and_costs_nothing(ahead):
    ahead.model.answer({"narrative": AHEAD, "next": []})
    ahead.round()
    ahead.clock.move_to(SUNDAY_NIGHT)
    r = ahead.opened()
    assert (r["narrative"], r["cached"], len(ahead.model.sent)) == (AHEAD, True, 1)


def test_the_next_monday_last_weeks_are_not_read(ahead):
    ahead.round()
    ahead.clock.move_to(MONDAY + timedelta(days=4))
    ahead.reviews.build({ADA}, force=True, week=True)     # refreshed on Friday, three days ago by the next Monday
    ahead.clock.move_to(MONDAY.replace(hour=4) + timedelta(days=7))
    assert ahead.opened()["missing"] is True


def test_without_the_week_a_read_finds_only_todays(ahead):
    ahead.round()
    ahead.clock.move_to(MONDAY + timedelta(days=1))
    assert ahead.opened(week=False)["missing"] is True


def test_one_written_again_later_in_the_week_is_the_one_read(ahead):
    ahead.model.answer({"narrative": AHEAD, "next": []}, {"narrative": LATER, "next": []})
    ahead.round()
    ahead.clock.move_to(MONDAY + timedelta(days=2))
    ahead.reviews.build({ADA}, force=True, week=True)     # refreshed on Wednesday
    ahead.clock.move_to(MONDAY + timedelta(days=3))
    assert ahead.opened()["narrative"] == LATER


def test_an_earlier_days_review_says_the_week_it_looked_back_on_as_it_said_it(ahead):
    ahead.week.handled = 2
    ahead.round()
    ahead.week.handled = 5                                # what was filed away since
    ahead.clock.move_to(MONDAY + timedelta(days=3))
    r = ahead.opened()
    assert (r["stats"]["handled"], r["week_start"]) == (2, (MONDAY - timedelta(days=7)).isoformat())


# ── the table ─────────────────────────────────────────────────────────────────────────────────────

def test_a_review_written_on_monday_morning_is_kept_past_the_end_of_its_week(data_dir):
    from mailmind.assistant.adapters import sqlite as answers
    from mailmind.bootstrap.mailbox import store

    def save(at):
        """A review written at `at`, which forgets those older than the table keeps."""
        with time_machine.travel(at, tick=False):
            c = store.conn()
            try:
                answers.save_review(c, ADA, at.date().isoformat(), "fp", AHEAD, "[]", "{}", "[]", "[]", "m", at.isoformat())
                c.commit()
            finally:
                c.close()

    def kept():
        c = store.conn()
        try:
            return [r["day"] for r in c.execute("SELECT day FROM reviews ORDER BY day")]
        finally:
            c.close()

    save(MONDAY.replace(hour=5))
    save(SUNDAY_NIGHT)                                    # another written at the end of the week
    assert kept() == ["2026-11-09", "2026-11-15"]
    save(MONDAY.replace(hour=5, minute=1) + timedelta(days=7))
    assert kept() == ["2026-11-15", "2026-11-16"]
