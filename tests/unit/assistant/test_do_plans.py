"""A bulk command's confirm carries out what its preview froze: by message key, once, within ten minutes, and
never what a request names. The preview's own guards are in characterization/assistant/test_do_plan.py and test_http_ai.py."""
from datetime import datetime, timedelta, timezone

import time_machine

from mailmind.assistant.application import plans
from mailmind.assistant.domain import action_plan


# -- the book ----------------------------------------------------------------------------------------

def test_the_book_keeps_the_newest_plans():
    book = plans.PlanBook()
    made = [book.open("archive", None, [(1, "INBOX")]) for _ in range(action_plan.KEPT + 2)]
    assert book.take(made[0].id) is None and book.take(made[1].id) is None
    assert all(book.take(p.id) == p for p in made[2:])


def test_a_plans_id_is_not_guessable_and_each_is_its_own():
    book = plans.PlanBook()
    a, b = book.open("archive", None, []), book.open("archive", None, [])
    assert a.id != b.id and len(a.id) >= 32


# -- when it can't -----------------------------------------------------------------------------------

NOON = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def test_a_plan_can_be_confirmed_once():
    book = plans.PlanBook()
    plan = book.open("archive", None, [(1, "INBOX")])
    assert book.take(plan.id) == plan
    assert book.take(plan.id) is None


def test_a_plan_lasts_ten_minutes():
    with time_machine.travel(NOON, tick=False) as clock:
        book = plans.PlanBook()
        plan = book.open("archive", None, [(1, "INBOX")])
        clock.shift(timedelta(seconds=action_plan.TTL))
        assert book.take(plan.id) is None


def test_a_plan_is_good_up_to_then():
    with time_machine.travel(NOON, tick=False) as clock:
        book = plans.PlanBook()
        plan = book.open("archive", None, [(1, "INBOX")])
        clock.shift(timedelta(seconds=action_plan.TTL - 1))
        assert book.take(plan.id) == plan
