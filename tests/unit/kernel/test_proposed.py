"""What someone proposes for a calendar or a to-do list: read with care, within a window, and on one line."""
from datetime import date, timedelta

import pytest

from mailmind.kernel import proposed

TODAY = date(2026, 11, 2)


@pytest.mark.parametrize("text, want", [
    ("2026-11-12", "2026-11-12"),
    ("  2026-11-12  ", "2026-11-12"),
    ("2026-11-12T15:00", "2026-11-12T15:00"),
    ("2026-11-12 15:00", "2026-11-12T15:00"),
    ("2026-11-12T15:00:30", "2026-11-12T15:00"),
    ("2026-11-12T15:00:00-05:00", "2026-11-12T15:00-05:00"),
    ("2026-11-12T15:00Z", "2026-11-12T15:00+00:00"),
])
def test_a_date_or_a_time_reads_as_iso_text(text, want):
    assert proposed.read_moment(text, TODAY) == want


@pytest.mark.parametrize("text", [
    "", "   ", "next Tuesday", "2026-13-01", "2026-02-30", "20261112", "2026-W46-4", "2026-11-12T25:00", "2026-11-12T15",
    "2026-11-12T15:00 tomorrow", None, 20261112, ["2026-11-12"], {"date": "2026-11-12"}, True,
])
def test_what_wont_read_is_none_and_never_raises(text):
    assert proposed.read_moment(text, TODAY) is None
    assert proposed.read_date(text, TODAY) is None


@pytest.mark.parametrize("text", ["0001-01-01", "9999-12-31", "9999-12-31T23:59+14:00", "0001-01-01T00:00-14:00", "1999-01-01", "2040-01-01"])
def test_a_date_far_from_today_is_dropped_rather_than_moved_to_the_edge(text):
    assert proposed.read_moment(text, TODAY) is None
    assert proposed.read_date(text, TODAY) is None


def test_the_window_runs_to_its_edges_and_no_further():
    assert proposed.read_date((TODAY - proposed.BEHIND).isoformat(), TODAY)
    assert proposed.read_date((TODAY + proposed.AHEAD).isoformat(), TODAY)
    assert proposed.read_date((TODAY - proposed.BEHIND - timedelta(days=1)).isoformat(), TODAY) is None
    assert proposed.read_date((TODAY + proposed.AHEAD + timedelta(days=1)).isoformat(), TODAY) is None


def test_a_time_reads_as_its_day_for_a_date():
    assert proposed.read_date("2026-11-12T15:00:00-05:00", TODAY) == "2026-11-12"


@pytest.mark.parametrize("start, end, fits", [
    ("2026-11-12T15:00", "2026-11-12T16:00", True),
    ("2026-11-12T15:00", "2026-11-12T15:00", False),
    ("2026-11-12T15:00", "2026-11-12T14:00", False),
    ("2026-11-12", "2026-11-12", True),
    ("2026-11-12", "2026-11-11", False),
    ("2026-11-12", "2026-12-13", True),
    ("2026-11-12", "2026-12-14", False),
    ("2026-11-12", "2026-11-12T15:00", False),
    ("2026-11-12T15:00", "2026-11-13", False),
    ("2026-11-12T15:00-05:00", "2026-11-12T16:00", False),
    ("2026-11-12T15:00", "2026-11-12T16:00+00:00", False),
    ("2026-11-12T15:00-05:00", "2026-11-12T21:00+01:00", False),
    ("2026-11-12T15:00-05:00", "2026-11-12T22:00+01:00", True),
    ("nonsense", "2026-11-12", False),
])
def test_what_can_end_what(start, end, fits):
    assert proposed.ends_after(start, end) is fits


def test_is_day_tells_a_day_from_a_time():
    assert proposed.is_day("2026-11-12") and not proposed.is_day("2026-11-12T15:00")


@pytest.mark.parametrize("text, want", [
    ("  Lunch   with\tLin ", "Lunch with Lin"),
    ("Lunch\r\nATTENDEE:mailto:x@example.test", "Lunch ATTENDEE:mailto:x@example.test"),
    ("a\rb", "a b"),
    ("a b c\x85d", "a b c d"),
    ("a\x00b\x07c", "a b c"),
    (None, ""),
    (7, ""),
    (["Lunch"], ""),
])
def test_text_comes_to_one_line(text, want):
    assert proposed.one_line(text, 100) == want


def test_text_is_held_to_its_cap():
    assert len(proposed.one_line("x" * 500, 10)) == 10
