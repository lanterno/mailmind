"""Today & next (calendar.agenda): which calendar invites in fetched mail make the brief's list, when
a repeating one next happens (ics.next_occurrence), and the recipient the Waiting view names
(mailbox.queries.waiting_in). The messages are built here; nothing touches IMAP. All addresses are
fictional."""
import itertools
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import pytest

from fixtures import world as w
from mailmind.bootstrap import calendar, mailbox
from mailmind.bootstrap.calendar import ics
from mailmind.bootstrap.mailbox import ingest, store
from mailmind.calendar.application import agenda as agenda_use_case
from mailmind.content import mime
from mailmind.mailbox.application.session import Headers

ACCT = "ada@example.test"
NOW = datetime(2026, 10, 30, 12, 0)          # a fixed local "now"
_uid = itertools.count(1)


def _ics(uid, start, end=None, summary="Studio sync", method="REQUEST", status="", all_day=False):
    fmt = "%Y%m%d" if all_day else "%Y%m%dT%H%M%S"
    val = (";VALUE=DATE:" if all_day else ":") + start.strftime(fmt)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"METHOD:{method}", "BEGIN:VEVENT", f"UID:{uid}",
             f"SUMMARY:{summary}", "LOCATION:Roof room", f"DTSTART{val}",
             "ORGANIZER;CN=Ren Takeda:mailto:ren@example.test"]
    if end:
        lines.append(("DTEND;VALUE=DATE:" if all_day else "DTEND:") + end.strftime(fmt))
    if status:
        lines.append(f"STATUS:{status}")
    return "\r\n".join(lines + ["END:VEVENT", "END:VCALENDAR", ""])


def _msg(cal=None, folder="INBOX", ts=None, account=ACCT, fetched=True, thread_id=None, to=""):
    """One message; with `cal`, an invite whose .ics part is stored as fetch_body would store it."""
    uid = next(_uid)
    c = store.conn()
    key = store.mint_key(c, account, f"<m{uid}@example.test>", "ren@example.test", None, "Invitation")
    cur = c.execute("INSERT INTO messages (account, kind, folder, uid, message_id, thread_id, ts, "
                    "from_addr, subject, recipients, message_key) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (account, "imap", folder, uid, f"<m{uid}@example.test>",
                     thread_id or f"t{uid}", int((ts or NOW - timedelta(days=1)).timestamp()),
                     "ren@example.test", "Invitation", to, key))
    pk = cur.lastrowid
    if cal is not None and fetched:
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = "ren@example.test", account, "Invitation"
        m.set_content("You're invited.")
        m.add_attachment(cal.encode(), maintype="text", subtype="calendar", filename="invite.ics")
        raw = m.as_bytes()
        store.store_body(c, pk, raw, NOW.isoformat())
        for a in mime.parse(raw)["attachments"]:
            c.execute("INSERT INTO attachments(message_pk,part_id,filename,content_type,size,content_id,"
                      "is_inline) VALUES(?,?,?,?,?,?,?)", (pk, a["part_id"], a["filename"],
                                                          a["content_type"], a["size"], a["content_id"], a["is_inline"]))
    c.commit()
    c.close()
    return pk


def _up(**kw):
    return calendar.agenda.upcoming(now=NOW, **kw)["items"]


def _named(item):
    """The copy a listed item names, as the page names one: its key, folder and uid."""
    return {"key": item["key"], "folder": item["folder"], "uid": item["uid"]}


def _copies(items):
    return [_named(x) for x in items]


def test_lists_future_events_soonest_first_with_local_times(data_dir):
    later = _msg(_ics("b", NOW + timedelta(days=2), summary="Hike"))
    soon = _msg(_ics("a", NOW + timedelta(hours=3), NOW + timedelta(hours=3, minutes=30)))
    items = _up()
    assert _copies(items) == [w.copy_of(soon), w.copy_of(later)], items
    a = items[0]
    assert a["summary"] == "Studio sync" and a["location"] == "Roof room"
    assert a["start"] == "2026-10-30T15:00" and a["end"] == "2026-10-30T15:30", a
    assert a["organizer"] == "Ren Takeda" and a["invite"] is True and a["account"] == ACCT


def test_an_event_that_has_ended_drops_out_but_one_in_progress_stays(data_dir):
    _msg(_ics("done", NOW - timedelta(hours=3), NOW - timedelta(hours=2)))
    on = _msg(_ics("now", NOW - timedelta(minutes=30), NOW + timedelta(minutes=30)))
    assert _copies(_up()) == [w.copy_of(on)]


def test_utc_times_come_back_as_local_wall_clock(machine_zone, data_dir):
    start = (NOW + timedelta(days=1)).astimezone().astimezone(timezone.utc)
    cal = _ics("z", start).replace("DTSTART:" + start.strftime("%Y%m%dT%H%M%S"),
                                   "DTSTART:" + start.strftime("%Y%m%dT%H%M%SZ"))
    _msg(cal)
    assert _up()[0]["start"] == (NOW + timedelta(days=1)).isoformat(timespec="minutes")


def test_an_all_day_event_today_stays_until_midnight(data_dir):
    today = NOW.replace(hour=0)
    pk = _msg(_ics("d", today, all_day=True))
    items = _up()
    assert _copies(items) == [w.copy_of(pk)] and items[0]["all_day"] is True
    assert items[0]["end"] == (today + timedelta(days=1)).isoformat(timespec="minutes")


def test_an_update_replaces_the_original(data_dir):
    _msg(_ics("u", NOW + timedelta(days=1), summary="Old time"), ts=NOW - timedelta(days=3))
    new = _msg(_ics("u", NOW + timedelta(days=2), summary="New time"), ts=NOW - timedelta(days=1))
    items = _up()
    assert [(_named(x), x["summary"]) for x in items] == [(w.copy_of(new), "New time")], items


def test_a_cancellation_removes_the_event(data_dir):
    _msg(_ics("c", NOW + timedelta(days=1)), ts=NOW - timedelta(days=3))
    _msg(_ics("c", NOW + timedelta(days=1), method="CANCEL", status="CANCELLED"), ts=NOW - timedelta(days=1))
    _msg(_ics("s", NOW + timedelta(days=1), status="CANCELLED"))
    assert _up() == []


def _weekly(uid="s", summary="Standup", extra=""):
    """A weekly series whose first meeting was two weeks ago, at NOW's time of day."""
    first = NOW - timedelta(days=14)
    return _ics(uid, first, first + timedelta(minutes=30), summary=summary).replace(
        "END:VEVENT", "RRULE:FREQ=WEEKLY\r\n" + extra + "END:VEVENT")


def _one(start, rid, **kw):
    """A message about one occurrence of series "s" (RECURRENCE-ID = its original start)."""
    return _ics("s", start, start + timedelta(minutes=30), **kw).replace(
        "END:VEVENT", "RECURRENCE-ID:" + rid.strftime("%Y%m%dT%H%M%S") + "\r\nEND:VEVENT")


def test_a_series_is_listed_at_its_next_occurrence(data_dir):
    pk = _msg(_weekly(), ts=NOW - timedelta(days=20))
    _msg(_weekly("p", "Yoga", "EXDATE:" + NOW.strftime("%Y%m%dT%H%M%S") + "\r\n"), ts=NOW - timedelta(days=20))
    items = _up()
    assert [(x["summary"], x["start"]) for x in items] == [
        ("Standup", "2026-10-30T12:00"), ("Yoga", "2026-11-06T12:00")], items   # today's in progress
    assert _named(items[0]) == w.copy_of(pk) and items[0]["recurrence"] == "Every week"


def test_moving_or_cancelling_one_occurrence_leaves_the_series(data_dir):
    _msg(_weekly(), ts=NOW - timedelta(days=20))
    _msg(_one(NOW + timedelta(days=1), NOW, summary="Standup (moved)"), ts=NOW - timedelta(days=2))
    _msg(_one(NOW + timedelta(days=7), NOW + timedelta(days=7), method="CANCEL", status="CANCELLED"),
         ts=NOW - timedelta(days=1))
    assert [(x["summary"], x["start"]) for x in _up()] == [
        ("Standup (moved)", "2026-10-31T12:00"), ("Standup", "2026-11-13T12:00")]


def _revised(uid, start, sequence, summary="Studio sync", **kw):
    return _ics(uid, start, summary=summary, **kw).replace("END:VEVENT", f"SEQUENCE:{sequence}\r\nEND:VEVENT")


def test_a_later_revision_counts_though_a_copy_of_an_older_one_came_after_it(data_dir):
    wins = _msg(_revised("r", NOW + timedelta(days=3), 2, summary="Moved here"), ts=NOW - timedelta(days=3))
    _msg(_revised("r", NOW + timedelta(days=1), 1, summary="Where it was"), ts=NOW - timedelta(days=1))
    items = _up()
    assert [(x["summary"], x["start"]) for x in items] == [("Moved here", "2026-11-02T12:00")] and _named(items[0]) == w.copy_of(wins)


def test_the_same_revision_goes_by_the_newest_message(data_dir):
    _msg(_revised("r", NOW + timedelta(days=3), 2, summary="Older copy"), ts=NOW - timedelta(days=3))
    _msg(_revised("r", NOW + timedelta(days=1), 2, summary="Newer copy"), ts=NOW - timedelta(days=1))
    assert [x["summary"] for x in _up()] == ["Newer copy"]


def test_a_stale_invite_doesnt_bring_back_what_a_later_revision_cancelled(data_dir):
    _msg(_revised("r", NOW + timedelta(days=3), 3, method="CANCEL", status="CANCELLED"), ts=NOW - timedelta(days=3))
    _msg(_revised("r", NOW + timedelta(days=3), 1), ts=NOW - timedelta(days=1))
    assert _up() == []


def test_a_revision_is_weighed_within_one_occurrence(data_dir):
    _msg(_weekly(), ts=NOW - timedelta(days=20))
    _msg(_one(NOW + timedelta(days=2), NOW, summary="Revised").replace("END:VEVENT", "SEQUENCE:2\r\nEND:VEVENT"), ts=NOW - timedelta(days=4))
    _msg(_one(NOW + timedelta(days=1), NOW, summary="First go").replace("END:VEVENT", "SEQUENCE:1\r\nEND:VEVENT"), ts=NOW - timedelta(days=3))
    assert [(x["summary"], x["start"]) for x in _up()] == [("Revised", "2026-11-01T12:00"), ("Standup", "2026-11-06T12:00")]


def test_a_stored_invite_from_outlook_shows_at_its_own_hour_in_your_zone(machine_zone, data_dir):
    start = NOW + timedelta(days=1)
    in_berlin = start.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Europe/Berlin"))
    text = _ics("o", start).replace("DTSTART:" + start.strftime("%Y%m%dT%H%M%S"),
                                    "DTSTART;TZID=W. Europe Standard Time:" + in_berlin.strftime("%Y%m%dT%H%M%S"))
    _msg(text)
    expected = start.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None).isoformat(timespec="minutes")
    assert _up()[0]["start"] == expected


def test_cancelling_the_series_drops_its_moved_occurrences_too(data_dir):
    _msg(_weekly(), ts=NOW - timedelta(days=20))
    _msg(_one(NOW + timedelta(days=1), NOW), ts=NOW - timedelta(days=2))
    _msg(_ics("s", NOW - timedelta(days=14), method="CANCEL", status="CANCELLED"), ts=NOW - timedelta(days=1))
    assert _up() == []


def test_a_rule_it_cannot_expand_shows_its_first_date_while_ahead(data_dir):
    rule = "RRULE:FREQ=HOURLY\r\n"
    _msg(_ics("a", NOW + timedelta(days=3)).replace("END:VEVENT", rule + "END:VEVENT"))
    _msg(_ics("b", NOW - timedelta(days=3)).replace("END:VEVENT", rule + "END:VEVENT"))
    assert [x["start"] for x in _up()] == ["2026-11-02T12:00"]


def _series_lines(uid, start, rule):
    return ["BEGIN:VEVENT", f"UID:{uid}", "SUMMARY:Series", f"DTSTART:{start:%Y%m%dT%H%M%S}", f"RRULE:{rule}", "END:VEVENT"]


def test_a_list_past_its_allowance_shows_a_series_at_its_first_date_alone(data_dir, monkeypatch):
    monkeypatch.setattr(agenda_use_case, "_EXPAND_SECONDS", -1)
    one_off = _msg(_ics("o", NOW + timedelta(days=1)))
    ahead = _msg(_ics("a", NOW + timedelta(days=2)).replace("END:VEVENT", "RRULE:FREQ=WEEKLY\r\nEND:VEVENT"))
    _msg(_ics("b", NOW - timedelta(days=3)).replace("END:VEVENT", "RRULE:FREQ=WEEKLY\r\nEND:VEVENT"))        # would next be in four days
    assert _copies(_up()) == [w.copy_of(one_off), w.copy_of(ahead)]


def test_one_message_of_series_that_each_use_up_their_budget_cant_hold_a_list_for_long(data_dir, monkeypatch):
    """Two hundred rules that never come to a date, in one message. Each stops at the reader's bound, which is no bound on
    the list: it took twenty seconds of a request thread."""
    monkeypatch.setattr(agenda_use_case, "_EXPAND_SECONDS", 0.2)
    lines = ["BEGIN:VCALENDAR", "METHOD:REQUEST"]
    for i in range(200):
        lines += _series_lines(f"s{i}", NOW - timedelta(days=3), "FREQ=DAILY;BYMONTH=2;BYMONTHDAY=30")
    _msg("\r\n".join([*lines, "END:VCALENDAR", ""]))
    ok = _msg(_ics("k", NOW + timedelta(days=1)))
    began = time.thread_time()
    assert w.copy_of(ok) in _copies(_up())
    assert time.thread_time() - began < 2


def test_trash_junk_unfetched_and_old_mail_are_not_read(data_dir):
    _msg(_ics("t", NOW + timedelta(days=1)), folder="Trash")
    _msg(_ics("j", NOW + timedelta(days=1)), folder="Spam")
    _msg(_ics("f", NOW + timedelta(days=1)), fetched=False)
    _msg(_ics("o", NOW + timedelta(days=1)), ts=NOW - timedelta(days=90))
    _msg()                                                    # mail without an invite
    assert _up() == []


def test_every_account_and_a_limit(data_dir):
    [_msg(_ics(f"m{i}", NOW + timedelta(days=i + 1))) for i in range(7)]
    _msg(_ics("x", NOW + timedelta(hours=1)), account="other@example.test")
    assert len(_up()) == 5 and _up()[0]["account"] == "other@example.test"


def test_a_broken_calendar_part_is_skipped(data_dir):
    _msg("not a calendar at all")
    ok = _msg(_ics("k", NOW + timedelta(days=1)))
    assert _copies(_up()) == [w.copy_of(ok)]


BERLIN = ZoneInfo("Europe/Berlin")
SUN = datetime(2026, 11, 1, 12, 0, tzinfo=BERLIN)   # the Sunday after Berlin leaves summer time
SAT = datetime(2026, 10, 31, 12, 0, tzinfo=BERLIN)


def _next(rule, extra="", start="20261005T100000", end="20261005T103000", after=SUN):
    """Next occurrence of a Monday 10:00–10:30 Berlin event that first ran on Oct 5, as ISO strings."""
    tz = ";TZID=Europe/Berlin:" if "T" in start else ";VALUE=DATE:"
    inv = ics.parse("\r\n".join(["BEGIN:VCALENDAR", "BEGIN:VEVENT", "UID:r", "SUMMARY:Standup",
                                 "DTSTART" + tz + start, "DTEND" + tz + end] + ([f"RRULE:{rule}"] if rule else [])
                                + ([extra] if extra else []) + ["END:VEVENT", "END:VCALENDAR", ""]))
    got = ics.next_occurrence(inv, after)
    return got and (got[0].isoformat(timespec="minutes"), got[1].isoformat(timespec="minutes"))


def test_a_weekly_event_keeps_its_wall_clock_time_and_length_across_daylight_saving():
    assert _next("FREQ=WEEKLY") == ("2026-11-02T10:00+01:00", "2026-11-02T10:30+01:00")


@pytest.mark.parametrize("rule, start", [
    ("FREQ=WEEKLY;INTERVAL=2", "2026-11-02T10:00+01:00"),     # Oct 5, 19, Nov 2
    ("FREQ=WEEKLY;INTERVAL=3", "2026-11-16T10:00+01:00"),     # Oct 5, 26, Nov 16
    ("FREQ=WEEKLY;BYDAY=WE,MO", "2026-11-02T10:00+01:00"),
])
def test_a_weekly_event_keeps_its_wall_clock_time_across_daylight_saving(rule, start):
    assert _next(rule)[0] == start


@pytest.mark.parametrize("rule, extra, start", [
    ("FREQ=WEEKLY;COUNT=5", "", "2026-11-02T10:00+01:00"),
    ("FREQ=WEEKLY;UNTIL=20261102T090000Z", "", "2026-11-02T10:00+01:00"),   # inclusive
    ("FREQ=WEEKLY", "EXDATE;TZID=Europe/Berlin:20261102T100000", "2026-11-09T10:00+01:00"),
])
def test_count_until_and_exdate_end_or_skip_occurrences(rule, extra, start):
    assert _next(rule, extra)[0] == start


@pytest.mark.parametrize("rule", ["FREQ=WEEKLY;COUNT=4",     # Oct 5 – Oct 26
                                  "FREQ=WEEKLY;UNTIL=20261102T085959Z"])
def test_count_and_until_end_a_series(rule):
    assert _next(rule) is None


@pytest.mark.parametrize("rule, after, start", [
    ("FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR", SAT, "2026-11-02T10:00+01:00"),
    ("FREQ=MONTHLY", SUN, "2026-11-05T10:00+01:00"),
    ("FREQ=MONTHLY;BYDAY=1TU", SUN, "2026-11-03T10:00+01:00"),
    ("FREQ=MONTHLY;BYDAY=-1FR", SUN, "2026-11-27T10:00+01:00"),
    ("FREQ=MONTHLY;BYMONTHDAY=31", SUN, "2026-12-31T10:00+01:00"),   # November has no 31st
    ("FREQ=YEARLY", SUN, "2027-10-05T10:00+02:00"),
])
def test_daily_monthly_and_yearly_rules(rule, after, start):
    assert _next(rule, after=after)[0] == start


def test_an_occurrence_in_progress_is_still_next():
    during = datetime(2026, 11, 2, 10, 15, tzinfo=BERLIN)
    assert _next("FREQ=DAILY", after=during)[0] == "2026-11-02T10:00+01:00"


def test_an_all_day_series_and_a_floating_one():
    assert _next("FREQ=WEEKLY", start="20261005", end="20261006",
                 after=datetime(2026, 11, 2, 18, 0)) == ("2026-11-02T00:00", "2026-11-03T00:00")
    inv = ics.parse("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:f\r\nDTSTART:20261005T100000\r\n"
                    "RRULE:FREQ=WEEKLY\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    assert ics.next_occurrence(inv, datetime(2026, 11, 1, 12, 0))[0] == datetime(2026, 11, 2, 10, 0)


@pytest.mark.parametrize("rule", ["FREQ=WEEKLY;BYDAY=XX", "FREQ=HOURLY", "FREQ=YEARLY;INTERVAL=9000"])   # the last is past year 9999
def test_a_rule_it_cannot_read_gives_none_not_a_wrong_date(rule):
    assert _next(rule) is None


def test_bysetpos_picks_the_day_of_the_month():
    assert _next("FREQ=MONTHLY;BYDAY=MO,TU;BYSETPOS=1") == ("2026-11-02T10:00+01:00", "2026-11-02T10:30+01:00")


def test_a_one_off_event_is_its_own_only_occurrence():
    assert _next("") is None                                                 # Oct 5 is over
    assert _next("", start="20261102T100000", end="20261102T103000")[0] == "2026-11-02T10:00+01:00"


def _when_said(*times):
    return "\r\n".join(["BEGIN:VCALENDAR", "BEGIN:VEVENT", "UID:w", "SUMMARY:Odd", *times, "END:VEVENT", "END:VCALENDAR", ""])


@pytest.mark.parametrize("start, end", [
    ("DTSTART;TZID=Europe/Berlin:20261102T100000", "DTEND;TZID=Nowhere/Land:20261102T103000"),
    ("DTSTART;TZID=Nowhere/Land:20261102T100000", "DTEND;TZID=Europe/Berlin:20261102T103000"),
    ("DTSTART:20261102T100000Z", "DTEND:20261102T103000"),
    ("DTSTART:20261102T100000", "DTEND:20261102T103000Z"),
])
def test_an_end_that_floats_where_its_start_doesnt_counts_by_its_wall_clock(start, end):
    inv = ics.parse(_when_said(start, end))
    got = ics.next_occurrence(inv, datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc))
    assert got is not None, inv
    assert got[1] - got[0] == timedelta(minutes=30)


@pytest.mark.parametrize("times", [
    ("DTSTART:00010101T000000Z",),
    ("DTSTART:99991231T235959Z",),
    ("DTSTART:00010101T000000Z", "DTEND:99990101T000000Z"),
    ("DTSTART:20261001T100000", "DTEND:99991231T235959"),
    ("DTSTART:99991231T100000", "DURATION:P7D"),
    ("DTSTART:20261001T100000", "RRULE:FREQ=DAILY;UNTIL=99991231T235959Z"),
    ("DTSTART:20261001T100000", "RRULE:FREQ=YEARLY;INTERVAL=9000"),
    ("DTSTART;TZID=Asia/Tokyo:00010101T000000", "RRULE:FREQ=YEARLY"),
])
def test_a_time_at_the_edge_of_a_datetime_costs_only_that_event_its_place(machine_zone, data_dir, times):
    """In a zone that pushes it past year 1 or 9999, a stranger's invite took the whole list down with it."""
    _msg(_when_said(*times), ts=NOW - timedelta(days=2))
    ok = _msg(_ics("k", NOW + timedelta(days=1)))
    assert w.copy_of(ok) in _copies(_up())


class _FakeSession:
    """Answers ingest._process's two reads (headers, then the whole message) from `raws`."""
    def __init__(self, raws):
        self.raws = raws

    def headers(self, uids, fields, gmail_thread=False):
        return [Headers(int(u), frozenset(), self.raws[int(u)].split(b"\n\n")[0] + b"\n\n") for u in uids]

    def body(self, uid):
        return self.raws[int(uid)]


def test_ingest_keeps_an_invite_so_it_is_listed_unopened(data_dir):
    def raw(n, cal=None):
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = "Ren <ren@example.test>", ACCT, "Invitation"
        m["Date"], m["Message-ID"] = "Fri, 30 Oct 2026 09:00:00 +0000", f"<in{n}@example.test>"
        m.set_content("You're invited.")
        if cal:
            m.add_attachment(cal.encode(), maintype="text", subtype="calendar", filename="invite.ics")
        return m.as_bytes()
    ingest._process(_FakeSession({1: raw(1, _ics("in", NOW + timedelta(days=1))), 2: raw(2)}),
                    {"email": ACCT, "kind": "imap"}, ["1", "2"], "INBOX", with_body=True)
    c = store.conn()
    rows = {r["uid"]: r for r in c.execute("SELECT pk, uid, body_fetched, seen FROM messages")}
    c.close()
    assert rows[1]["body_fetched"] == 1 and rows[1]["seen"] == 0, dict(rows[1])
    assert rows[2]["body_fetched"] == 0, "mail without an invite is stored as before"
    assert _copies(_up()) == [w.copy_of(rows[1]["pk"])]


def _alt(cal, attach=False):
    """An invite the way Outlook sends it (an unnamed text/calendar part of the alternative) or,
    with `attach`, the way Google does (that part plus an invite.ics file)."""
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "ren@example.test", ACCT, "Invitation"
    m.set_content("Join us")
    m.add_alternative(cal, subtype="calendar", params={"method": "REQUEST"}, cte="base64")
    if attach:
        m.make_mixed()
        m.add_attachment(cal.encode(), maintype="application", subtype="ics", filename="invite.ics")
    return m.as_bytes()


def test_an_inline_only_invite_is_recorded_once_and_listed(data_dir):
    cal = _ics("ol", NOW + timedelta(days=1), summary="Quarterly review")
    out = mime.parse(_alt(cal))["attachments"]
    assert [(a["filename"], a["content_type"], a["is_inline"]) for a in out] == [("invite.ics", "text/calendar", 1)], out
    both = mime.parse(_alt(cal, attach=True))["attachments"]
    assert [a["content_type"] for a in both] == ["application/ics"], "no second chip when a file carries it"
    pk = _msg()
    c = store.conn()
    ingest._keep_body(c, pk, _alt(cal), mime.parse(_alt(cal)))
    c.commit()
    c.close()
    assert [(_named(x), x["summary"]) for x in _up()] == [(w.copy_of(pk), "Quarterly review")]


def test_waiting_names_who_a_sent_thread_waits_on(data_dir):
    pk = _msg(thread_id="w1", to="Lio Park <lio@example.test>, Aoi <aoi@example.test>")
    c = store.conn()
    c.execute("INSERT INTO followups(account, thread_id, kind, state, first_sent_ts, due_ts) "
              "VALUES (?,?,?,?,?,?)", (ACCT, "w1", "sent", "due", int(NOW.timestamp()), int(NOW.timestamp())))
    c.commit()
    c.close()
    items = mailbox.queries.waiting_in(ACCT, 0, 10)["items"]
    assert [(_named(x), x["kind"], x["to"]) for x in items] == [(w.copy_of(pk), "sent", "Lio Park <lio@example.test>")], items
