"""Three things decision D11 had fixed in search: a date means midnight where you are, snoozed mail
is out of sight until it wakes, and Search and Ask rank by one table of weights."""
import glob
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pytest

from mailmind.bootstrap import discovery
from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.bootstrap import attention
from mailmind.kernel.snoozed import not_snoozed_sql

RECEIPT = "ada-inbox-05-receipt.eml"
ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..")


@pytest.fixture
def in_new_york(monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def found(app, q, scope="all"):
    """What a search finds, each as the page names it: by its key and folder."""
    return [{"key": i["key"], "folder": i["folder"], "uid": i["uid"]} for i in app.get(f"/api/search?scope={scope}&q={quote(q)}&limit=200").json["items"]]


def bound(q):
    _, where, params = discovery.build(q)
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
    from mailmind.bootstrap.mailbox import store
    pk = world.pk(RECEIPT)
    evening = int(datetime(2026, 11, 2, 2, tzinfo=timezone.utc).timestamp())       # 21:00 on Nov 1 in New York
    c = store.conn()
    c.execute("UPDATE messages SET ts=? WHERE pk=?", (evening, pk))
    c.commit()
    c.close()
    assert world.copy(RECEIPT) in found(app, "before:2026-11-02")
    assert world.copy(RECEIPT) not in found(app, "after:2026-11-02")


# -- snoozed --------------------------------------------------------------

def snooze(app, copy):
    until = int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp())
    assert app.post("/api/snooze", {**copy, "until": until}).json["ok"]


def test_snoozed_mail_isnt_found_until_it_wakes(app, world):
    subject = world.row(RECEIPT)["subject"].split()[0]
    copy = world.copy(RECEIPT)
    assert copy in found(app, f"subject:{subject}")
    snooze(app, copy)
    assert copy not in found(app, f"subject:{subject}")
    assert copy not in found(app, f"from:{world.row(RECEIPT)['from_addr']}")
    app.clock.shift(timedelta(days=2))
    attention.snoozing.wake_due()
    assert copy in found(app, f"subject:{subject}")


def test_asking_for_snoozed_mail_finds_it(app, world):
    copy, other = world.copy(RECEIPT), world.copy("ada-inbox-02-lunch-confirm.eml")
    snooze(app, copy)
    assert found(app, "is:snoozed") == [copy]
    assert copy in found(app, f"is:snoozed subject:{world.row(RECEIPT)['subject'].split()[0]}")
    assert copy not in found(app, "-is:snoozed") and other in found(app, "-is:snoozed")


def test_a_command_doesnt_reach_snoozed_mail(app, world, ai):
    from_addr = world.row(RECEIPT)["from_addr"]
    def planned():
        ai.queue(fa.answer({"intent": "act", "verb": "archive", "filter": f"from:{from_addr}"}))
        return app.post("/api/ai/do-plan", {"cmd": "archive them"}).json
    assert planned()["count"] == 1
    snooze(app, world.copy(RECEIPT))
    assert planned().get("count", 0) == 0


def test_the_grammar_says_where_snoozed_mail_went():
    assert discovery.visible(["m.seen = ?"]) == ["m.seen = ?", not_snoozed_sql()]
    for asks in ("is:snoozed", "-is:snoozed"):
        where = discovery.build(asks)[1]
        assert discovery.visible(where) == where


# -- ranking --------------------------------------------------------------

def test_search_and_ask_rank_by_one_table():
    sources = [*glob.glob(os.path.join(ROOT, "*.py")), *glob.glob(os.path.join(ROOT, "mailmind", "**", "*.py"), recursive=True)]
    named = {os.path.basename(p) for p in sources if "bm25(" in open(p, encoding="utf-8").read()}
    assert named == {"fts.py"}


def test_the_table_covers_every_indexed_column_and_ranks_them(world):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    columns = [r[1] for r in c.execute("PRAGMA table_info(msg_fts)")]
    c.close()
    assert columns == ["subject", "from_name", "from_addr", "recipients", "snippet", "body_text"]
    weights = [float(x) for x in discovery.BM25.removeprefix("bm25(msg_fts,").removesuffix(")").split(",")]
    assert len(weights) == len(columns)
    by = dict(zip(columns, weights))
    assert by["subject"] > by["from_addr"] == by["from_name"] > by["body_text"] > by["recipients"] > by["snippet"]


def test_a_hit_in_the_subject_ranks_above_one_in_the_body(app, world):
    world.deliver(w.ADA, "INBOX", w.variant("ada-inbox-05-receipt.eml", Message_ID="<rank-1@example.test>",
                                            Subject="A zanzibar mention"))
    world.deliver(w.ADA, "INBOX", (b"From: Lin <lin@example.test>\r\nTo: ada@example.test\r\nSubject: Plans\r\n"
                                   b"Message-ID: <rank-2@example.test>\r\nDate: Mon, 02 Nov 2026 09:00:00 +0000\r\n"
                                   b"Content-Type: text/plain\r\n\r\nNothing here but one zanzibar in a few words.\r\n"))
    items = app.get("/api/search?scope=all&q=zanzibar&limit=10").json["items"]
    assert [i["subject"] for i in items] == ["A zanzibar mention", "Plans"]
