"""Things decision D11 had fixed in search: a date means midnight where you are, and snoozed mail is
out of sight until it wakes."""
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pytest

import search
from fakes import anthropic_api as fa

RECEIPT = "ada-inbox-05-receipt.eml"


@pytest.fixture
def in_new_york(monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def found(app, q, scope="all"):
    return [i["pk"] for i in app.get(f"/api/search?scope={scope}&q={quote(q)}&limit=200").json["items"]]


def bound(q):
    _, where, params = search.build(q)
    return where, params


# -- dates ----------------------------------------------------------------

def test_a_date_is_midnight_where_you_are(in_new_york):
    midnight = int(datetime(2026, 11, 2, 5, tzinfo=timezone.utc).timestamp())      # New York is at -05:00
    assert bound("after:2026-11-02") == (["m.ts >= ?"], [midnight])
    assert bound("before:2026-11-02") == (["m.ts < ?"], [midnight])


def test_daylight_saving_is_the_date_s_own(in_new_york):
    midnight = int(datetime(2026, 7, 1, 4, tzinfo=timezone.utc).timestamp())       # and at -04:00 in July
    assert bound("after:2026-07-01") == (["m.ts >= ?"], [midnight])


def test_a_zone_you_name_is_kept(in_new_york):
    assert bound("after:2026-11-02T00:00:00+00:00")[1] == [int(datetime(2026, 11, 2, tzinfo=timezone.utc).timestamp())]


def test_with_utc_as_the_zone_nothing_moves():
    assert bound("after:2026-11-02")[1] == [int(datetime(2026, 11, 2, tzinfo=timezone.utc).timestamp())]


@pytest.mark.parametrize("bad", ["after:2026-13-45", "before:soon", "after:0000-01-01"])
def test_a_date_that_isnt_one_bounds_nothing(bad):
    assert bound(bad) == ([], [])


def test_mail_from_this_evening_is_before_tomorrow_here(app, world, in_new_york):
    import store
    pk = world.pk(RECEIPT)
    evening = int(datetime(2026, 11, 2, 2, tzinfo=timezone.utc).timestamp())       # 21:00 on Nov 1 in New York
    c = store.conn()
    c.execute("UPDATE messages SET ts=? WHERE pk=?", (evening, pk))
    c.commit()
    c.close()
    assert pk in found(app, "before:2026-11-02")
    assert pk not in found(app, "after:2026-11-02")


# -- snoozed --------------------------------------------------------------

def snooze(app, pk):
    until = int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp())
    assert app.post("/api/snooze", {"pk": pk, "until": until}).json["ok"]


def test_snoozed_mail_isnt_found_until_it_wakes(app, world):
    import server
    subject = world.row(RECEIPT)["subject"].split()[0]
    pk = world.pk(RECEIPT)
    assert pk in found(app, f"subject:{subject}")
    snooze(app, pk)
    assert pk not in found(app, f"subject:{subject}")
    assert pk not in found(app, f"from:{world.row(RECEIPT)['from_addr']}")
    app.clock.shift(timedelta(days=2))
    server.wake_snoozed()
    assert pk in found(app, f"subject:{subject}")


def test_asking_for_snoozed_mail_finds_it(app, world):
    pk, other = world.pk(RECEIPT), world.pk("ada-inbox-02-lunch-confirm.eml")
    snooze(app, pk)
    assert found(app, "is:snoozed") == [pk]
    assert pk in found(app, f"is:snoozed subject:{world.row(RECEIPT)['subject'].split()[0]}")
    assert pk not in found(app, "-is:snoozed") and other in found(app, "-is:snoozed")


def test_a_command_doesnt_reach_snoozed_mail(app, world, ai):
    pk = world.pk(RECEIPT)
    from_addr = world.row(RECEIPT)["from_addr"]
    ai.queue(fa.answer({"intent": "act", "verb": "archive", "filter": f"from:{from_addr}"}))
    assert pk in (app.post("/api/ai/do-plan", {"cmd": "archive them"}).json.get("pks") or [])
    snooze(app, pk)
    ai.queue(fa.answer({"intent": "act", "verb": "archive", "filter": f"from:{from_addr}"}))
    assert pk not in (app.post("/api/ai/do-plan", {"cmd": "archive them"}).json.get("pks") or [])


def test_the_grammar_says_where_snoozed_mail_went():
    assert search.visible(["m.seen = ?"]) == ["m.seen = ?", "m.snoozed_until IS NULL"]
    where = search.build("is:snoozed")[1]
    assert search.visible(where) == where
