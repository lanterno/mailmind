"""What a snooze holds to, with no database, no clock and no I/O."""
from datetime import datetime, timedelta, timezone

import pytest

from mailmind.attention.domain import snooze
from mailmind.kernel.errors import Refused

ZONE = timezone(timedelta(hours=2))
NOW = datetime(2026, 11, 4, 10, 30, tzinfo=ZONE)          # a Wednesday
TS = int(NOW.timestamp())
YEAR = snooze.MAX_AHEAD_DAYS * 86400


def at(*args):
    return int(datetime(*args, tzinfo=ZONE).timestamp())


# ── its window: (now, now + 366 days] ─────────────────────────────────────────
@pytest.mark.parametrize("until, ok", [(TS, False), (TS + 1, True), (TS + YEAR, True), (TS + YEAR + 1, False), (TS - 1, False)])
def test_the_window_is_open_at_now_and_closed_a_year_on(until, ok):
    assert snooze.in_window(until, TS) is ok
    assert snooze.wake_time({"until": until}, NOW) == (until if ok else None)


def test_the_window_is_366_days():
    assert snooze.MAX_AHEAD_DAYS == 366


@pytest.mark.parametrize("until", ["2026-11-05T09:00:00+02:00", str(at(2026, 11, 5, 9))])
def test_a_time_may_come_as_iso_or_as_seconds(until):
    assert snooze.wake_time({"until": until}, NOW) == at(2026, 11, 5, 9)


def test_an_iso_time_outside_the_window_is_refused():
    assert snooze.wake_time({"until": "2026-11-03T09:00:00+02:00"}, NOW) is None
    assert snooze.wake_time({"until": "2028-11-03T09:00:00+02:00"}, NOW) is None


@pytest.mark.parametrize("until", ["soon", "", [1], {}, True, float("inf"), float("-inf"), float("nan")])
def test_what_isnt_a_time_is_refused(until):
    assert snooze.wake_time({"until": until}, NOW) is None


def test_asking_for_nothing_is_refused():
    assert snooze.wake_time({}, NOW) is None


# ── presets ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("preset, want", [
    ("later_today", (2026, 11, 4, 13)),
    ("tomorrow", (2026, 11, 5, 8)),
    ("weekend", (2026, 11, 7, 8)),
    ("next_week", (2026, 11, 9, 8)),
])
def test_a_preset_wakes_at_its_time(preset, want):
    assert snooze.wake_time({"preset": preset}, NOW) == at(*want)


def test_later_today_crossing_midnight_is_tomorrow_morning():
    assert snooze.wake_time({"preset": "later_today"}, datetime(2026, 11, 4, 22, 0, tzinfo=ZONE)) == at(2026, 11, 5, 8)
    assert snooze.wake_time({"preset": "later_today"}, datetime(2026, 11, 4, 20, 59, tzinfo=ZONE)) == at(2026, 11, 4, 23)


def test_on_its_own_day_a_weekend_or_a_next_week_is_a_week_off():
    assert snooze.wake_time({"preset": "weekend"}, datetime(2026, 11, 7, 7, 0, tzinfo=ZONE)) == at(2026, 11, 14, 8)
    assert snooze.wake_time({"preset": "next_week"}, datetime(2026, 11, 9, 7, 0, tzinfo=ZONE)) == at(2026, 11, 16, 8)


def test_a_preset_goes_before_a_time():
    assert snooze.wake_time({"preset": "tomorrow", "until": TS + 60}, NOW) == at(2026, 11, 5, 8)


def test_an_unknown_preset_is_refused():
    assert snooze.wake_time({"preset": "someday", "until": TS + 60}, NOW) is None


# ── waking ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("until, due", [(TS - 1, True), (TS, True), (TS + 1, False)])
def test_it_is_due_at_its_time(until, due):
    assert snooze.is_due(until, TS) is due


@pytest.mark.parametrize("seen, marks", [(1, True), (True, True), (0, False), (None, False)])
def test_only_read_mail_asks_the_server_to_mark_it_unread(seen, marks):
    assert snooze.marks_unread_on_waking(seen) is marks


# ── what a request names ──────────────────────────────────────────────────────
def test_a_thread_needs_its_account_and_goes_before_any_pks():
    assert snooze.named_thread({"account": "ada@example.test", "thread_id": "T", "pk": 3}) == ("ada@example.test", "T")
    assert snooze.named_thread({"thread_id": "T", "pk": 3}) is None
    assert snooze.named_thread({"account": "ada@example.test", "pk": 3}) is None


def test_more_pks_than_one_request_may_name_are_refused_never_cut_short():
    assert len(snooze.named_pks({"pks": list(range(snooze.BULK_CAP))})) == snooze.BULK_CAP == 500
    with pytest.raises(Refused):
        snooze.named_pks({"pks": list(range(snooze.BULK_CAP + 1))})


def test_pks_go_before_a_pk():
    assert snooze.named_pks({"pks": ["1", 2], "pk": 3}) == [1, 2]
    assert snooze.named_pks({"pks": "not a list", "pk": 3}) == [3]
    assert snooze.named_pks({"pk": 0}) == [0]
    assert snooze.named_pks({}) == []


# ── Undo: `previous` is kept ──────────────────────────────────────────────────
def message(key, until=None):
    return {"message_key": key, "folder": "INBOX", "snooze_until": until}


def test_the_entry_keeps_what_each_was_and_where_it_is():
    assert snooze.undo_entry([1, 2, 3], [message(10), message(11, until=99), None]) == {
        "kind": "snooze", "pks": [1, 2, 3], "where": [[10, "INBOX"], [11, "INBOX"], None], "prev_until": [None, 99, None],
        "label": "Snoozed"}


def test_undo_goes_back_to_what_it_was_by_where_it_is():
    entry = snooze.undo_entry([1, 2], [message(10), message(11, until=99)])
    assert snooze.restorations(entry) == [snooze.Restoration(1, [10, "INBOX"], None), snooze.Restoration(2, [11, "INBOX"], 99)]


def test_undo_passes_over_a_message_that_had_no_row():
    entry = snooze.undo_entry([1, 2], [None, message(11)])
    assert [r.pk for r in snooze.restorations(entry)] == [2]


def test_an_entry_from_before_where_goes_by_pk():
    assert snooze.restorations({"pks": [1, 2], "prev_until": [99]}) == [snooze.Restoration(1, None, 99), snooze.Restoration(2, None, None)]


def test_an_entry_with_no_previous_time_clears_it():
    assert snooze.restorations({"pks": [1], "prev_until": [0]}) == [snooze.Restoration(1, None, None)]
    assert snooze.restorations({"pks": [1]}) == [snooze.Restoration(1, None, None)]
