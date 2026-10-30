"""Today & next (server.upcoming): which calendar invites in fetched mail make the brief's list, and
the recipient the Waiting view names (server.waiting_in). Hermetic — the database is a temp file and
the messages are built here; nothing touches IMAP. All addresses are fictional.
Run: python3 test_upcoming.py"""
import os
import tempfile
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import store
store.DB = os.path.join(tempfile.mkdtemp(), "mailmind.db")   # before anything opens a connection
import mailparse
import server

ACCT = "ada@example.test"
NOW = datetime(2026, 10, 30, 12, 0)          # a fixed local "now"
_uid = [0]


def _fresh():
    store.DB = os.path.join(tempfile.mkdtemp(), "mailmind.db")
    store.init()


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
    _uid[0] += 1
    c = store.conn()
    cur = c.execute("INSERT INTO messages (account, kind, folder, uid, message_id, thread_id, ts, "
                    "from_addr, subject, recipients) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (account, "imap", folder, _uid[0], f"<m{_uid[0]}@example.test>",
                     thread_id or f"t{_uid[0]}", int((ts or NOW - timedelta(days=1)).timestamp()),
                     "ren@example.test", "Invitation", to))
    pk = cur.lastrowid
    if cal is not None and fetched:
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = "ren@example.test", account, "Invitation"
        m.set_content("You're invited.")
        m.add_attachment(cal.encode(), maintype="text", subtype="calendar", filename="invite.ics")
        raw = m.as_bytes()
        store.store_body(c, pk, raw, NOW.isoformat())
        for a in mailparse.parse(raw)["attachments"]:
            c.execute("INSERT INTO attachments(message_pk,part_id,filename,content_type,size,content_id,"
                      "is_inline) VALUES(?,?,?,?,?,?,?)", (pk, a["part_id"], a["filename"],
                                                          a["content_type"], a["size"], a["content_id"], a["is_inline"]))
    c.commit()
    c.close()
    return pk


def _up(**kw):
    return server.upcoming(now=NOW, **kw)["items"]


def test_lists_future_events_soonest_first_with_local_times():
    _fresh()
    later = _msg(_ics("b", NOW + timedelta(days=2), summary="Hike"))
    soon = _msg(_ics("a", NOW + timedelta(hours=3), NOW + timedelta(hours=3, minutes=30)))
    items = _up()
    assert [x["pk"] for x in items] == [soon, later], items
    a = items[0]
    assert a["summary"] == "Studio sync" and a["location"] == "Roof room"
    assert a["start"] == "2026-10-30T15:00" and a["end"] == "2026-10-30T15:30", a
    assert a["organizer"] == "Ren Takeda" and a["invite"] is True and a["account"] == ACCT


def test_an_event_that_has_ended_drops_out_but_one_in_progress_stays():
    _fresh()
    _msg(_ics("done", NOW - timedelta(hours=3), NOW - timedelta(hours=2)))
    on = _msg(_ics("now", NOW - timedelta(minutes=30), NOW + timedelta(minutes=30)))
    assert [x["pk"] for x in _up()] == [on]


def test_utc_times_come_back_as_local_wall_clock():
    _fresh()
    start = (NOW + timedelta(days=1)).astimezone().astimezone(timezone.utc)
    cal = _ics("z", start).replace("DTSTART:" + start.strftime("%Y%m%dT%H%M%S"),
                                   "DTSTART:" + start.strftime("%Y%m%dT%H%M%SZ"))
    _msg(cal)
    assert _up()[0]["start"] == (NOW + timedelta(days=1)).isoformat(timespec="minutes")


def test_an_all_day_event_today_stays_until_midnight():
    _fresh()
    today = NOW.replace(hour=0)
    pk = _msg(_ics("d", today, all_day=True))
    items = _up()
    assert [x["pk"] for x in items] == [pk] and items[0]["all_day"] is True
    assert items[0]["end"] == (today + timedelta(days=1)).isoformat(timespec="minutes")


def test_an_update_replaces_the_original():
    _fresh()
    _msg(_ics("u", NOW + timedelta(days=1), summary="Old time"), ts=NOW - timedelta(days=3))
    new = _msg(_ics("u", NOW + timedelta(days=2), summary="New time"), ts=NOW - timedelta(days=1))
    items = _up()
    assert [(x["pk"], x["summary"]) for x in items] == [(new, "New time")], items


def test_a_cancellation_removes_the_event():
    _fresh()
    _msg(_ics("c", NOW + timedelta(days=1)), ts=NOW - timedelta(days=3))
    _msg(_ics("c", NOW + timedelta(days=1), method="CANCEL", status="CANCELLED"), ts=NOW - timedelta(days=1))
    _msg(_ics("s", NOW + timedelta(days=1), status="CANCELLED"))
    assert _up() == []


def test_trash_junk_unfetched_and_old_mail_are_not_read():
    _fresh()
    _msg(_ics("t", NOW + timedelta(days=1)), folder="Trash")
    _msg(_ics("j", NOW + timedelta(days=1)), folder="Spam")
    _msg(_ics("f", NOW + timedelta(days=1)), fetched=False)
    _msg(_ics("o", NOW + timedelta(days=1)), ts=NOW - timedelta(days=90))
    _msg()                                                    # mail without an invite
    assert _up() == []


def test_account_filter_and_limit():
    _fresh()
    mine = [_msg(_ics(f"m{i}", NOW + timedelta(days=i + 1))) for i in range(7)]
    _msg(_ics("x", NOW + timedelta(hours=1)), account="other@example.test")
    assert [x["pk"] for x in _up(account=ACCT)] == mine[:5]
    assert len(_up()) == 5 and _up()[0]["account"] == "other@example.test"


def test_a_broken_calendar_part_is_skipped():
    _fresh()
    _msg("not a calendar at all")
    ok = _msg(_ics("k", NOW + timedelta(days=1)))
    assert [x["pk"] for x in _up()] == [ok]


def test_waiting_names_who_a_sent_thread_waits_on():
    _fresh()
    pk = _msg(thread_id="w1", to="Lio Park <lio@example.test>, Aoi <aoi@example.test>")
    c = store.conn()
    c.execute("INSERT INTO followups(account, thread_id, kind, state, anchor_pk, first_sent_ts, due_ts) "
              "VALUES (?,?,?,?,?,?,?)", (ACCT, "w1", "sent", "due", pk, int(NOW.timestamp()), int(NOW.timestamp())))
    c.commit()
    c.close()
    items = server.waiting_in(ACCT, 0, 10)["items"]
    assert [(x["pk"], x["kind"], x["to"]) for x in items] == [(pk, "sent", "Lio Park <lio@example.test>")], items


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL", fn.__name__, repr(e))
        except Exception as e:
            failed += 1
            print("ER:U", fn.__name__, repr(e))
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    sys.exit(1 if failed else 0)
