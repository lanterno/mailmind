"""The body backfill's rounds against the world's fake server: what one failing body, or one account's trouble, may do to
the mail behind it. It may cost that message some waiting, and that account a turn, and no one else anything."""

import pytest

from mailmind.mailbox.domain import body_backfill
from mailmind.mailbox.domain.body_backfill import SetAside


# -- the policy alone: values in and values out -----------------------------------

def test_each_account_takes_its_turn_newest_first():
    assert body_backfill.in_turn([["a1", "a2", "a3"], ["b1"], ["c1", "c2"]]) == ["a1", "b1", "c1", "a2", "c2", "a3"]


def test_a_round_stops_at_its_limit_with_every_account_still_served():
    a, b, c = [f"a{i}" for i in range(12)], [f"b{i}" for i in range(12)], [f"c{i}" for i in range(12)]
    round_ = body_backfill.in_turn([a, b, c])
    assert len(round_) == body_backfill.ROUND
    assert round_[:3] == ["a0", "b0", "c0"] and sum(x.startswith("c") for x in round_) == 4


def test_nothing_waiting_is_an_empty_round():
    assert body_backfill.in_turn([]) == [] and body_backfill.in_turn([[], []]) == []


def test_a_body_not_given_waits_the_long_wait_and_forgets_any_lost_connection():
    assert body_backfill.not_given(100) == SetAside(100 + body_backfill.NOT_GIVEN_WAIT)


def test_a_lost_connection_waits_a_minute_then_twice_that_each_time_up_to_the_long_wait():
    one, waits = None, []
    for at in range(12):
        one = body_backfill.connection_lost(one, at)
        waits.append(one.until - at)
    assert waits[:4] == [60, 120, 240, 480]
    assert waits == sorted(waits) and waits[-1] == body_backfill.NOT_GIVEN_WAIT
    assert one.connections_lost == 12


def test_a_wait_that_is_over_lets_the_message_be_asked_for_and_keeps_its_count():
    set_aside = {1: SetAside(50, 3), 2: SetAside(200)}
    assert body_backfill.still_waiting(set_aside, 100) == [2]
    assert body_backfill.still_waiting(set_aside, 50) == [2]        # its time has come
    assert body_backfill.connection_lost(set_aside[1], 100).connections_lost == 4


@pytest.mark.parametrize("going, resting, wait", [(True, False, 8), (True, True, 8), (False, True, 60), (False, False, 600)])
def test_how_long_until_the_next_round(going, resting, wait):
    assert body_backfill.next_round(going=going, resting=resting) == wait
