"""The actions that never touch the server: snooze and its presets, mute, follow-ups and Reply
Later, with the views that list them and the timers that wake them. The clock is frozen at
conftest.NOW (Monday 2026-11-02 12:00 UTC) and moved on where a test needs time to pass. The
golden files are in tests/golden/http_local/.
"""
from datetime import datetime, timedelta, timezone

import pytest

from fixtures import world as w
from mailmind.bootstrap import attention
from mailmind.attention.domain import followups as follow_up_rules


def rows(sql, *args):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, args)]
    finally:
        c.close()


def post(app, golden, label, path, body):
    return golden.record(label, app.post(path, body))


def listing(app, golden, label, account, folder):
    """The view's rows as (subject, seen), the part these tests change."""
    from urllib.parse import quote
    r = app.get(f"/api/messages?account={account}&folder={quote(folder)}")
    golden.note(label, {"status": r.status, "rows": [[i["subject"], i["seen"]] for i in r.json.get("items", [])]})


# ── snooze ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("preset", ["later_today", "tomorrow", "weekend", "next_week"])
def test_snooze_presets_from_monday_noon(app, world, preset):
    pk = world.pk("ada-inbox-05-receipt.eml")
    r = app.post("/api/snooze", {"pk": pk, "preset": preset})
    wake = datetime.fromtimestamp(r.json["until"], timezone.utc)
    assert r.json["ok"] and r.json["count"] == 1
    assert wake == {"later_today": datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc),
                    "tomorrow": datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc),
                    "weekend": datetime(2026, 11, 7, 8, 0, tzinfo=timezone.utc),
                    "next_week": datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)}[preset]


def test_later_today_after_nine_at_night_means_tomorrow_morning(app, world):
    app.clock.move_to(datetime(2026, 11, 2, 22, 30, tzinfo=timezone.utc))
    r = app.post("/api/snooze", {"pk": world.pk("ada-inbox-05-receipt.eml"), "preset": "later_today"})
    assert datetime.fromtimestamp(r.json["until"], timezone.utc) == datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc)


def test_snooze_list_wake_and_undo(app, golden, world):
    receipt, folded = world.pk("ada-inbox-05-receipt.eml"), world.pk("ada-inbox-09-rfc2047-folded.eml")
    until = int(datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc).timestamp())
    post(app, golden, "snooze one until 18:00", "/api/snooze", {"pk": receipt, "until": until})
    post(app, golden, "snooze one until an ISO time", "/api/snooze",
         {"pk": folded, "until": "2026-11-03T09:30:00+00:00"})
    listing(app, golden, "Ada's INBOX while both sleep", w.ADA, "INBOX")
    listing(app, golden, "Ada's Snoozed", w.ADA, "__snoozed__")
    post(app, golden, "undo the ISO snooze", "/api/undo", {})
    listing(app, golden, "Ada's Snoozed after undo", w.ADA, "__snoozed__")

    app.clock.move_to(datetime(2026, 11, 2, 18, 0, 1, tzinfo=timezone.utc))
    attention.snoozing.wake_due()
    golden.note("after 18:00 the receipt wakes unread", rows(
        "SELECT subject, seen, snoozed_until FROM messages WHERE pk=?", receipt))
    listing(app, golden, "Ada's Snoozed after the wake", w.ADA, "__snoozed__")


def test_snooze_refusals_and_targets(app, golden, world):
    pk = world.pk("ada-inbox-05-receipt.eml")
    for label, body in (
        ("an unknown preset", {"pk": pk, "preset": "someday"}),
        ("a time in the past", {"pk": pk, "until": int(datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp())}),
        ("a time over a year away", {"pk": pk, "until": "2027-12-01T00:00:00+00:00"}),
        ("a time that isn't one", {"pk": pk, "until": "soon"}),
        ("no time at all", {"pk": pk}),
        ("no target", {"preset": "tomorrow"}),
        ("a missing message", {"pk": 999999, "preset": "tomorrow"}),
        ("a whole thread", {"account": w.ADA, "thread_id": world.row("ada-inbox-01-lunch.eml")["thread_id"],
                            "preset": "tomorrow"}),
        ("a list of pks", {"pks": [world.pk("bea-inbox-01-client.eml"), world.pk("cy-inbox-03-unrelated.eml")],
                           "preset": "tomorrow"}),
        ("a pk that isn't a number", {"pk": "x", "preset": "tomorrow"}),
    ):
        post(app, golden, f"snooze: {label}", "/api/snooze", body)
    golden.note("snoozed rows", rows("SELECT account, subject, snoozed_until FROM messages "
                                     "WHERE snoozed_until IS NOT NULL ORDER BY pk"))
    post(app, golden, "unsnooze the lunch thread", "/api/unsnooze",
         {"account": w.ADA, "thread_id": world.row("ada-inbox-01-lunch.eml")["thread_id"]})
    post(app, golden, "unsnooze nothing", "/api/unsnooze", {})
    golden.note("snoozed rows after", rows("SELECT account, subject FROM messages "
                                           "WHERE snoozed_until IS NOT NULL ORDER BY pk"))


# ── mute ─────────────────────────────────────────────────────────────────

def test_mute_hides_a_thread_marks_it_read_and_undo_restores_it(app, golden, world):
    lunch = world.row("ada-inbox-02-lunch-confirm.eml")
    post(app, golden, "mute the lunch thread by pk", "/api/mute", {"pk": lunch["pk"]})
    golden.note("the thread's rows", rows("SELECT folder, subject, seen FROM messages WHERE thread_id=? ORDER BY pk",
                                          lunch["thread_id"]))
    post(app, golden, "mute it again", "/api/mute", {"account": w.ADA, "thread_id": lunch["thread_id"]})
    listing(app, golden, "Ada's INBOX", w.ADA, "INBOX")
    listing(app, golden, "Ada's Muted", w.ADA, "__muted__")
    post(app, golden, "undo the mute", "/api/undo", {})
    golden.note("the thread's rows after undo", rows(
        "SELECT folder, subject, seen FROM messages WHERE thread_id=? ORDER BY pk", lunch["thread_id"]))
    golden.note("muted", rows("SELECT * FROM muted"))
    post(app, golden, "mute, then unmute", "/api/mute", {"pk": lunch["pk"]})
    post(app, golden, "unmute", "/api/unmute", {"pk": lunch["pk"]})
    golden.note("muted after unmute", rows("SELECT * FROM muted"))
    for label, body in (("an account that isn't configured", {"account": "zed@example.test", "thread_id": "t"}),
                        ("no thread", {"account": w.ADA}),
                        ("a missing message", {"pk": 999999}),
                        ("nothing", {})):
        post(app, golden, f"mute: {label}", "/api/mute", body)


# ── follow-ups and Reply Later ─────────────────────────────────────────────

def test_waiting_follow_ups_and_dismissal(app, golden, world, ai):
    from mailmind.bootstrap import assistant
    # A received thread waits on you only once AI has called it important or action-needed; the
    # fake API calls every message action-needed.
    golden.note("messages summarized", assistant.assessments.summarize_pending(limit=100, workers=1))
    attention.followups.tick()
    golden.note("follow-ups at Monday noon", rows(
        "SELECT account, thread_id, kind, state, due_ts FROM followups ORDER BY account, thread_id"))
    app.clock.shift(timedelta(days=4))
    attention.followups.tick()
    golden.note("follow-ups four days later", rows(
        "SELECT account, thread_id, kind, state, due_ts FROM followups ORDER BY account, thread_id"))
    for acct in (w.ADA, w.BEA, w.CY):
        listing(app, golden, f"{acct}'s Waiting", acct, "__waiting__")
    due = rows("SELECT account, thread_id FROM followups WHERE state='due' ORDER BY account, thread_id")
    if due:
        post(app, golden, "dismiss the first", "/api/followups/dismiss", due[0])
    post(app, golden, "dismiss nothing", "/api/followups/dismiss", {})
    golden.note("follow-ups after dismissal", rows(
        "SELECT account, thread_id, kind, state FROM followups ORDER BY account, thread_id"))


def test_reply_later_pins_and_unpins(app, golden, world):
    client = world.row("bea-inbox-01-client.eml")
    post(app, golden, "reply later to Ivo", "/api/replylater", {"pk": client["pk"]})
    golden.note("follow-ups", rows("SELECT account, thread_id, kind, state, anchor_pk FROM followups"))
    listing(app, golden, "Bea's Reply Later", w.BEA, "__replylater__")
    post(app, golden, "un-pin it", "/api/replylater",
         {"account": w.BEA, "thread_id": client["thread_id"], "on": False})
    listing(app, golden, "Bea's Reply Later after", w.BEA, "__replylater__")
    post(app, golden, "reply later to nothing", "/api/replylater", {})


def test_reply_later_records_when_the_thread_last_moved(app, world):
    client = world.row("bea-inbox-01-client.eml")           # its thread also holds a later draft of Bea's
    app.post("/api/replylater", {"pk": client["pk"]})
    [row] = rows("SELECT last_msg_ts FROM followups")
    assert row["last_msg_ts"] == client["ts"]


def test_a_received_nudge_stops_after_the_expiry_window(app, world, ai):
    from mailmind.bootstrap import assistant
    assistant.assessments.summarize_pending(limit=100, workers=1)
    quilt = world.row("ada-archive-01-old-thread.eml")      # from Lin, read, unanswered, 20 Oct
    attention.followups.tick()
    assert rows("SELECT state FROM followups WHERE thread_id=?", quilt["thread_id"]) == [{"state": "due"}]
    app.clock.move_to(datetime.fromtimestamp(quilt["ts"], timezone.utc)
                      + timedelta(days=follow_up_rules.EXPIRE_DAYS, hours=1))
    attention.followups.tick()
    assert rows("SELECT state FROM followups WHERE thread_id=?", quilt["thread_id"]) == []
