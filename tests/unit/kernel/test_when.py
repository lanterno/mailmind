"""When something happened, or will, as the app says it (mailmind.kernel.when): each step of each scale, and what a time
that won't read comes to. The clock stands at Monday 2026-11-02 12:00 UTC, the suite's time zone."""
from datetime import datetime, timedelta, timezone

import pytest
import time_machine

from mailmind.kernel.when import ago, now_iso, rel_time, rel_time_future

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def frozen():
    with time_machine.travel(NOW, tick=False):
        yield


def before(**delta):
    return (NOW - timedelta(**delta)).isoformat()


def test_now_is_said_in_utc():
    assert now_iso() == "2026-11-02T12:00:00+00:00"


@pytest.mark.parametrize("iso, said", [
    (before(minutes=59), "just now"),
    (before(hours=3, minutes=5), "3h ago"),
    (before(hours=23), "23h ago"),
    (before(days=1, hours=2), "yesterday"),
    (before(days=6), "6d ago"),
    (before(days=7), "1w ago"),
    (before(days=29), "4w ago"),
    (before(days=31), "Oct 02"),
    ("2026-11-02T09:00:00", "3h ago"),
    ("2026-11-02T13:00:00+01:00", "just now"),
], ids=repr)
def test_a_time_past_is_said_on_the_scale_it_falls_in(iso, said):
    assert rel_time(iso) == said


@pytest.mark.parametrize("iso, said", [(None, ""), ("", ""), ("2026-11-0x and then some", "2026-11-0x")], ids=repr)
def test_a_time_past_that_wont_read_says_what_it_can(iso, said):
    assert rel_time(iso) == said


@pytest.mark.parametrize("ts, said", [
    (NOW.timestamp() - 59, "0m"),
    (NOW.timestamp() - 5 * 60, "5m"),
    (NOW.timestamp() - 3 * 3600, "3h"),
    (NOW.timestamp() - 2 * 86400, "2d"),
    (str(int(NOW.timestamp() - 2 * 86400)), "2d"),
    (NOW.timestamp() + 600, "0m"),                     # ahead of the clock: no time at all yet
    (None, "a while"),
    (0, "a while"),
], ids=repr)
def test_how_long_ago_is_the_largest_whole_unit(ts, said):
    assert ago(ts) == said


@pytest.mark.parametrize("delta, said", [
    (timedelta(minutes=-5), "soon"),
    (timedelta(minutes=30), "in 30m"),
    (timedelta(hours=3), "today 3:00 PM"),
    (timedelta(hours=21), "tomorrow 9:00 AM"),
    (timedelta(days=4, hours=-3), "Fri 9:00 AM"),
    (timedelta(days=30), "Dec 2"),
], ids=repr)
def test_a_time_to_come_is_said_on_the_scale_it_falls_in(delta, said):
    assert rel_time_future((NOW + delta).timestamp()) == said
    assert rel_time_future(str(int((NOW + delta).timestamp()))) == said


def test_a_time_to_come_that_wont_read_says_nothing():
    assert rel_time_future("next week") == ""
