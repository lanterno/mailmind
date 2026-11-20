"""When something happened, or will, as the app says it (mailmind.kernel.when): each step of each scale, what a time
that won't read comes to, and how long ago in the person's own days on either side of their midnight, in zones east and
west of UTC (the Rust core's crates/mm-kernel/tests/ago.rs). The clock stands at Monday 2026-11-02 12:00 UTC, the suite's
time zone."""
import os
import time
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
    (before(seconds=89), "just now"),                 # within a minute and a half, as the page says it
    (before(seconds=90), "1m ago"),
    (before(minutes=59), "59m ago"),                  # within the hour, minutes: not just now
    (before(hours=3, minutes=5), "3h ago"),
    (before(hours=11, minutes=59), "11h ago"),
    (before(hours=23), "yesterday"),                  # the day before, though not 24 hours back
    (before(days=1, hours=2), "yesterday"),
    (before(days=6), "6d ago"),
    (before(days=7), "1w ago"),
    (before(days=29), "4w ago"),
    (before(days=31), "Oct 02"),
    ("2026-11-02T09:00:00", "3h ago"),
    ("2026-11-02T13:00:00+01:00", "just now"),
    ("2026-11-02T12:00:30+00:00", "just now"),        # a little ahead of the clock, the same day
    ("2026-11-03T12:00:00+00:00", "Nov 03"),          # a day still to come is its date
], ids=repr)
def test_a_time_past_is_said_on_the_scale_it_falls_in(iso, said):
    assert rel_time(iso) == said


# The zones the midnight cases run in, each with the offset it has in early November 2026, as an ISO time writes it.
ZONES = [("Pacific/Auckland", "+13:00"), ("Asia/Kolkata", "+05:30"), ("Europe/Paris", "+01:00"), ("UTC", "+00:00"),
         ("America/Los_Angeles", "-08:00")]


@pytest.fixture
def machine_zone(monkeypatch):
    """This machine's zone, which is the person's, for the test; the suite's own UTC after it."""
    def set_to(name):
        monkeypatch.setenv("TZ", name)
        time.tzset()
    yield set_to
    os.environ["TZ"] = "UTC"
    time.tzset()


@pytest.mark.parametrize("zone, written", ZONES, ids=[z for z, _ in ZONES])
@pytest.mark.parametrize("wall, said", [
    ("2026-11-03T00:19:30", "just now"),              # Today
    ("2026-11-03T00:05:00", "15m ago"),               # Today
    ("2026-11-02T23:50:00", "yesterday"),             # Yesterday: not just now, half an hour back
    ("2026-11-02T00:30:00", "yesterday"),             # Yesterday: not 23h ago
    ("2026-11-01T18:00:00", "2d ago"),                # Past week: not yesterday, 30 hours back
    ("2026-10-27T22:00:00", "1w ago"),                # Earlier: not 6d ago
], ids=repr)
def test_just_after_midnight_the_words_agree_with_the_days_the_list_heads(machine_zone, zone, written, wall, said):
    machine_zone(zone)
    twenty_past_midnight = datetime.fromisoformat(f"2026-11-03T00:20:00{written}")
    with time_machine.travel(twenty_past_midnight, tick=False):
        assert rel_time(wall + written) == said


def test_a_time_written_in_another_zone_falls_on_the_persons_day(machine_zone):
    # 05:00 in New York on the 2nd is 10:00 UTC, and 23:00 in Auckland; it is 23:30 UTC, 12:30 on the 3rd there.
    sent = "2026-11-02T05:00:00-05:00"
    with time_machine.travel(datetime(2026, 11, 2, 23, 30, tzinfo=timezone.utc), tick=False):
        assert rel_time(sent) == "13h ago"
        machine_zone("Pacific/Auckland")
        assert rel_time(sent) == "yesterday"


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
