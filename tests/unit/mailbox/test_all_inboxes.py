"""Every account's Inbox as one list and who you wait on across accounts: one query each, where the browser used to ask
each account and merge what came back, a page of each with no way further."""
import json
import time
from datetime import datetime, timedelta, timezone

from fixtures import world as w
from mailmind.bootstrap import mailbox
from mailmind.bootstrap.mailbox import store
from mailmind.platform import settings

LUNCH = "ada-inbox-02-lunch-confirm.eml"
RECEIPT = "ada-inbox-05-receipt.eml"
NEWSLETTER = "ada-inbox-03-newsletter.eml"
EVERYONE = (w.ADA, w.BEA, w.CY)


def inboxes(app, query=""):
    return app.get(f"/api/inboxes{query}").json


def of_account(app, email, query=""):
    return app.get(f"/api/messages?account={email}&folder=INBOX&limit=500{query}").json["items"]


def disable(email):
    path = settings.current().config
    held = json.loads(open(path, encoding="utf-8").read())
    next(a for a in held["accounts"] if a["email"] == email)["disabled"] = True
    with open(path, "w", encoding="utf-8") as f:
        json.dump(held, f)


def execute(sql, *args):
    c = store.conn()
    c.execute(sql, args)
    c.commit()
    c.close()


def named(item):
    """The copy a row of a list names, by its key and folder."""
    return item["key"], item["folder"]


def pk_of(item):
    """The row a list's row names, for an order the database breaks ties in by pk."""
    c = store.conn()
    try:
        return c.execute("SELECT pk FROM messages WHERE message_key=? AND folder=?", named(item)).fetchone()["pk"]
    finally:
        c.close()


def test_every_inbox_is_one_list_newest_first_and_each_row_names_its_account(app):
    got = inboxes(app, "?limit=500")
    assert {i["account"] for i in got["items"]} == set(EVERYONE)
    assert [i["ts"] for i in got["items"]] == sorted((i["ts"] for i in got["items"]), reverse=True)
    for email in EVERYONE:
        assert [named(i) for i in got["items"] if i["account"] == email] == [named(i) for i in of_account(app, email)]
    assert got["total"] == len(got["items"]) and got["has_more"] is False


def test_it_pages_through_all_of_it_each_row_once(app):
    whole = [named(i) for i in inboxes(app, "?limit=500")["items"]]
    assert len(whole) > 6
    pages, offset = [], 0
    while True:
        page = inboxes(app, f"?limit=3&offset={offset}")
        assert page["total"] == len(whole) and page["offset"] == offset
        pages += [named(i) for i in page["items"]]
        offset += 3
        if not page["has_more"]:
            break
    assert pages == whole


def test_grouped_it_holds_each_accounts_conversations(app):
    got = inboxes(app, "?limit=500&group=thread")
    assert got["grouped"] is True and got["total"] == len(got["items"])
    for email in EVERYONE:
        alone = of_account(app, email, "&group=thread")
        assert [(named(i), i["count"]) for i in got["items"] if i["account"] == email] == [(named(i), i["count"]) for i in alone]


def test_a_thread_id_means_something_only_within_its_account(app):
    before = inboxes(app, "?limit=500&group=thread")
    one_each = {i["account"]: named(i) for i in before["items"]}
    for key, folder in one_each.values():
        execute("UPDATE messages SET thread_id='rf:same' WHERE message_key=? AND folder=?", key, folder)
    after = inboxes(app, "?limit=500&group=thread")
    assert {named(i) for i in after["items"] if i["thread_id"] == "rf:same"} == set(one_each.values())
    assert after["total"] == len(after["items"])


def test_it_leaves_out_what_a_folders_list_leaves_out(app, world):
    gone = {name: world.copy(name) for name in (LUNCH, RECEIPT, NEWSLETTER)}
    until = int((datetime.now(timezone.utc) + timedelta(hours=6)).timestamp())
    assert app.post("/api/snooze", {**gone[LUNCH], "until": until}).json["ok"]
    assert app.post("/api/mute", gone[RECEIPT]).json["ok"]
    execute("UPDATE messages SET pending_removal=1 WHERE pk=?", world.pk(NEWSLETTER))
    for query in ("?limit=500", "?limit=500&group=thread"):
        got = inboxes(app, query)
        assert not {named(i) for i in got["items"]} & {named(copy) for copy in gone.values()}
        assert got["total"] == len(got["items"])


def test_a_disabled_account_isnt_in_it(app):
    disable(w.BEA)
    assert {i["account"] for i in inboxes(app, "?limit=500")["items"]} == {w.ADA, w.CY}


def test_with_no_account_it_is_empty(app):
    for email in EVERYONE:
        disable(email)
    got = inboxes(app)
    assert (got["items"], got["total"], got["has_more"]) == ([], 0, False)
    assert app.get("/api/waiting-on-others").json["items"] == []


def test_an_account_on_its_first_scan_holds_up_no_one(app, world, monkeypatch):
    monkeypatch.setattr(mailbox.folders, "FIRST_SYNC_WAIT", 30)
    execute("DELETE FROM sync_state WHERE account=? AND folder='INBOX'", w.BEA)
    world.server.stall("SELECT", user=w.BEA)
    try:
        began = time.perf_counter()
        got = inboxes(app, "?limit=500")
        assert time.perf_counter() - began < 10
        assert got["syncing"] is True
        assert {i["account"] for i in got["items"]} == set(EVERYONE)         # BEA's as they are stored
    finally:
        world.server.release_stalls()


def test_a_later_page_starts_no_scan(app, world):
    execute("DELETE FROM sync_state WHERE account=? AND folder='INBOX'", w.BEA)
    asked = len(world.server.commands)
    assert "syncing" not in inboxes(app, "?limit=2&offset=2")
    app.settle()
    assert len(world.server.commands) == asked


def follow_up(account, thread, kind, due):
    execute("INSERT INTO followups(account, thread_id, kind, state, first_sent_ts, due_ts) VALUES (?,?,?,?,?,?)",
            account, thread, kind, "due", due, due)


def waiting_threads(app):
    """Each account's newest Inbox thread, set waiting on someone. Oldest first, as the answer should be."""
    newest = {}
    for email in EVERYONE:
        newest[email] = of_account(app, email)[0]
    return sorted(newest.values(), key=lambda i: (i["ts"], pk_of(i)))


def test_who_you_wait_on_is_one_list_longest_wait_first(app):
    threads = waiting_threads(app)
    for i in threads:
        follow_up(i["account"], i["thread_id"], "sent", 1)
    got = app.get("/api/waiting-on-others").json
    assert [(named(i), i["account"], i["kind"]) for i in got["items"]] == [(named(i), i["account"], "sent") for i in threads]


def test_a_thread_waiting_on_you_or_pinned_isnt_someone_you_wait_on(app):
    threads = waiting_threads(app)
    follow_up(threads[0]["account"], threads[0]["thread_id"], "received", 1)
    follow_up(threads[1]["account"], threads[1]["thread_id"], "replylater", 1)
    assert app.get("/api/waiting-on-others").json["items"] == []


def test_who_you_wait_on_leaves_out_a_disabled_account_and_keeps_to_its_limit(app):
    threads = waiting_threads(app)
    for i in threads:
        follow_up(i["account"], i["thread_id"], "sent", 1)
    assert len(mailbox.queries.waiting_on_others(2)["items"]) == 2
    disable(w.BEA)
    assert w.BEA not in {i["account"] for i in app.get("/api/waiting-on-others").json["items"]}
