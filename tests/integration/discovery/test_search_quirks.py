"""Three things decision D11 had fixed in search: a date means midnight where you are, snoozed mail
is out of sight until it wakes, and Search and Ask rank by one table of weights."""
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pytest

from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.bootstrap import attention

RECEIPT = "ada-inbox-05-receipt.eml"


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


def test_a_hit_in_the_subject_ranks_above_one_in_the_body(app, world):
    world.deliver(w.ADA, "INBOX", w.variant("ada-inbox-05-receipt.eml", Message_ID="<rank-1@example.test>",
                                            Subject="A zanzibar mention"))
    world.deliver(w.ADA, "INBOX", (b"From: Lin <lin@example.test>\r\nTo: ada@example.test\r\nSubject: Plans\r\n"
                                   b"Message-ID: <rank-2@example.test>\r\nDate: Mon, 02 Nov 2026 09:00:00 +0000\r\n"
                                   b"Content-Type: text/plain\r\n\r\nNothing here but one zanzibar in a few words.\r\n"))
    items = app.get("/api/search?scope=all&q=zanzibar&limit=10").json["items"]
    assert [i["subject"] for i in items] == ["A zanzibar mention", "Plans"]
