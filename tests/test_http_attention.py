"""Every list the page draws from says what a message's verdict asks of you."""
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pytest

from fixtures import world as w

RECEIPT = "ada-inbox-05-receipt.eml"
FLAGS = ("needs_you", "reply_worthy", "commitment_worthy", "safe_to_file")


def in_a_day():
    return int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp())


def verdict(world, name, importance, category, action):
    import store
    c = store.conn()
    try:
        store.save_summary(c, world.pk(name), "A summary.", importance, category, action, "claude-opus-5-5",
                           "2026-11-02T11:00:00+00:00")
        c.commit()
    finally:
        c.close()


def answers(item):
    return tuple(item[f] for f in FLAGS)


def rows(app, world, route):
    if route == "digest":
        return app.get(f"/api/digest?account={w.ADA}").json["items"]
    if route == "folder":
        return app.get(f"/api/messages?account={w.ADA}&folder=INBOX&limit=100").json["items"]
    if route == "search":
        return app.get(f"/api/search?scope=all&account={w.ADA}&q={quote('from:example.test')}&limit=100").json["items"]
    return app.get(f"/api/messages?account={w.ADA}&folder=__snoozed__&limit=100").json["items"]


@pytest.mark.parametrize("route", ["digest", "folder", "search", "snoozed"])
@pytest.mark.parametrize("importance, category, action, expected", [
    (5, "action-needed", "Reply to Lin", (True, True, True, False)),
    (1, "info", "Read it", (True, False, False, False)),
    (4, "work-job", "", (True, True, False, False)),
    (3, "work-job", "Send the slides", (True, False, True, False)),
    (1, "info", "", (False, False, False, True)),
    (3, "finance", "", (False, False, False, False)),
])
def test_each_list_says_what_the_verdict_asks(app, world, route, importance, category, action, expected):
    verdict(world, RECEIPT, importance, category, action)
    pk = world.pk(RECEIPT)
    if route == "snoozed":
        assert app.post("/api/snooze", {"pk": pk, "until": in_a_day()}).json["ok"]
    [item] = [i for i in rows(app, world, route) if i["pk"] == pk]
    assert answers(item) == expected


@pytest.mark.parametrize("route", ["folder", "search", "snoozed"])
def test_a_message_with_no_verdict_asks_nothing(app, world, route):
    pk = world.pk(RECEIPT)
    if route == "snoozed":
        assert app.post("/api/snooze", {"pk": pk, "until": in_a_day()}).json["ok"]
    [item] = [i for i in rows(app, world, route) if i["pk"] == pk]
    assert answers(item) == (False, False, False, False)
