"""Removing an account over HTTP (POST /api/accounts/delete) may let go of what this Mac keeps of its mail too
(`delete_mail`): its stored copies with their bodies and attachments, the index's rows of them, what was said of them,
each context's state under its address, Undo's entries and the audit trails' records of it, and the AI's cards made for
it. Its mail on the server is untouched, and so is every other account's here. A sync reading its mail as it goes stops
there and keeps nothing, and the open tabs hear of the removal."""
import json
from urllib.parse import urlencode

from event_stream import Stream
from fixtures import world as w

LUNCH = "ada-inbox-01-lunch.eml"          # Ada's
SAMPLES = "ada-inbox-07-forward-rfc822.eml"   # Ada's, with an attachment
CLIENT = "bea-inbox-01-client.eml"        # Bea's
THREAD = "cy-inbox-01-thread-start.eml"   # Cy's

# A message only Ada's server has yet, from a person, so a sync reads its body.
MARKET = (b"From: Lin Mori <lin@example.test>\r\nTo: Ada Park <ada@example.test>\r\nSubject: Seen at the market\r\n"
          b"Date: Mon, 02 Nov 2026 09:00:00 +0000\r\nMessage-ID: <market-1@example.test>\r\n\r\nThe linen stall is back.\r\n")


def stored(account):
    """How many rows of `account`'s this Mac keeps in each table that keeps them under its address."""
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return {table: c.execute(f"SELECT count(*) FROM {table} WHERE account=?", (account,)).fetchone()[0]
                for table in ("messages", "message_identity", "sync_state")}
    finally:
        c.close()


def q(sql, *args):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return [tuple(r) for r in c.execute(sql, args)]
    finally:
        c.close()


def seed(*statements):
    """Rows written as the app would have, while the server is stopped: each (sql, args)."""
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        for sql, args in statements:
            c.execute(sql, args)
        c.commit()
    finally:
        c.close()


def heard_until_mute(tab, app, world):
    """What the tab heard, as (event, data), up to a mute set going now: a mark nothing before it is said after."""
    app.post("/api/mute", {"account": w.CY, "thread_id": world.row(THREAD)["thread_id"]})
    heard = []
    while True:
        head, data = tab.event().split("\n")[1:3]
        event = head.removeprefix("event: ")
        if event == "mute":
            return heard
        heard.append((event, json.loads(data.removeprefix("data: "))))


def entry(account, label):
    return {"kind": "move", "account": account, "label": label, "subject": label, "who": "lin@example.test"}


def test_removing_an_account_and_its_mail_lets_go_of_what_this_mac_keeps_of_it(app, world):
    on_server = len(world.server.messages(w.ADA, "INBOX"))
    assert app.get("/api/message/body?" + urlencode(world.copy(SAMPLES))).status == 200
    samples, lunch, client = world.pk(SAMPLES), world.key(LUNCH), world.key(CLIENT)
    assert q("SELECT count(*) FROM bodies WHERE message_pk=?", samples) == [(1,)]
    assert q("SELECT count(*) FROM attachments WHERE message_pk=?", samples)[0][0] > 0
    lunch_thread = world.row(LUNCH)["thread_id"]
    with app.stopped():
        seed(("INSERT OR REPLACE INTO assessments(message_key, summary, created_at) VALUES(?,?,?)",
              (lunch, "Lunch on Thursday", "2026-11-02T12:00:00+00:00")),
             ("INSERT OR REPLACE INTO assessments(message_key, summary, created_at) VALUES(?,?,?)",
              (client, "The contract", "2026-11-02T12:00:00+00:00")),
             ("INSERT INTO thread_summaries(account, thread_id, fingerprint, tldr) VALUES(?,?,?,?)",
              (w.ADA, lunch_thread, "fp", "Lunch")),
             ("INSERT INTO undo_entries(grp, entry, created_at) VALUES(NULL,?,?)",
              (json.dumps(entry(w.ADA, "Ada's own")), "2026-11-02T12:00:00+00:00")),
             ("INSERT INTO undo_entries(grp, entry, created_at) VALUES(?,?,?)",
              ("g-both", json.dumps({"kind": "bulk", "group": "g-both", "label": "Swept",
                                     "children": [entry(w.ADA, "Ada's swept"), entry(w.BEA, "Bea's swept")]}),
               "2026-11-02T12:00:00+00:00")),
             ("INSERT INTO undo_entries(grp, entry, created_at) VALUES(?,?,?)",
              ("g-ada", json.dumps({"kind": "bulk", "group": "g-ada", "label": "Swept",
                                    "children": [entry(w.ADA, "Ada's alone")]}), "2026-11-02T12:00:00+00:00")),
             ("INSERT INTO undo_entries(grp, entry, created_at) VALUES(NULL,?,?)",
              (json.dumps(entry(w.BEA, "Bea's own")), "2026-11-02T12:00:00+00:00")),
             ("INSERT INTO audit_events(trail, ts, body) VALUES('sweep',?,?)",
              ("2026-11-02T12:00:00+00:00", json.dumps({"kind": "sweep", "ts": "2026-11-02T12:00:00+00:00",
                                                         "account": w.ADA, "from": "lin@example.test",
                                                         "subject": "Lunch on Thursday?"}))),
             ("INSERT INTO audit_events(trail, ts, body) VALUES('sweep',?,?)",
              ("2026-11-02T12:00:00+00:00", json.dumps({"kind": "sweep", "ts": "2026-11-02T12:00:00+00:00",
                                                         "account": w.BEA, "from": "ivo@harbor.example.test",
                                                         "subject": "Contract draft v2"}))),
             ("INSERT INTO briefs(accounts, day, fingerprint, narrative, groups_json, created_at) VALUES(?,?,?,?,?,?)",
              (f"{w.ADA},{w.BEA}", "2026-11-02", "fp", "Lunch, and a contract", "[]", "2026-11-02T12:00:00+00:00")),
             ("INSERT INTO briefs(accounts, day, fingerprint, narrative, groups_json, created_at) VALUES(?,?,?,?,?,?)",
              (w.BEA, "2026-11-02", "fp", "A contract", "[]", "2026-11-02T12:00:00+00:00")),
             ("INSERT INTO sender_unsub(from_addr, raw, accounts, updated_at) VALUES(?,?,?,?)",
              ("news@example.test", "<https://news.example.test/out>", json.dumps([w.ADA]), "2026-11-02")),
             ("INSERT INTO sender_unsub(from_addr, raw, accounts, updated_at) VALUES(?,?,?,?)",
              ("shop@example.test", "<https://shop.example.test/out>", json.dumps([w.ADA, w.BEA]), "2026-11-02")))
    kept = stored(w.ADA)["messages"]
    assert kept > 0
    assert q("SELECT count(*) FROM msg_fts WHERE msg_fts MATCH 'Thursday'")[0][0] > 0

    r = app.post("/api/accounts/delete", {"email": w.ADA, "delete_mail": True})
    assert (r.status, r.json) == (200, {"ok": True, "removed": w.ADA, "mail_deleted": kept})
    assert stored(w.ADA) == {"messages": 0, "message_identity": 0, "sync_state": 0}
    assert world.where(LUNCH)["db"] == []
    assert q("SELECT count(*) FROM bodies WHERE message_pk=?", samples) == [(0,)]
    assert q("SELECT count(*) FROM attachments WHERE message_pk=?", samples) == [(0,)]
    assert q("SELECT count(*) FROM msg_fts WHERE msg_fts MATCH 'Thursday'") == [(0,)]
    assert q("SELECT message_key FROM assessments WHERE message_key IN (?,?)", lunch, client) == [(client,)]
    assert q("SELECT count(*) FROM thread_summaries WHERE account=?", w.ADA) == [(0,)]
    assert [json.loads(e)["label"] for (e,) in q("SELECT entry FROM undo_entries ORDER BY id")] == ["Swept", "Bea's own"]
    assert [c["label"] for c in json.loads(q("SELECT entry FROM undo_entries WHERE grp='g-both'")[0][0])["children"]] \
        == ["Bea's swept"]
    assert [json.loads(b)["account"] for (b,) in q("SELECT body FROM audit_events")] == [w.BEA]
    assert q("SELECT accounts FROM briefs") == [(w.BEA,)]
    assert [(a, json.loads(n)) for a, n in q("SELECT from_addr, accounts FROM sender_unsub")] \
        == [("shop@example.test", [w.BEA])]
    assert len(world.server.messages(w.ADA, "INBOX")) == on_server          # the server's mail is untouched
    assert [d["account"] for d in world.where(CLIENT)["db"]] == [w.BEA]      # and so is everyone else's here


def test_removing_an_account_alone_keeps_its_mail_here(app, world):
    kept = stored(w.ADA)
    r = app.post("/api/accounts/delete", {"email": w.ADA})
    assert (r.status, r.json) == (200, {"ok": True, "removed": w.ADA})
    assert stored(w.ADA) == kept


def test_an_account_no_one_saved_lets_go_of_nothing(app, world):
    kept = stored(w.ADA)
    r = app.post("/api/accounts/delete", {"email": "zed@example.test", "delete_mail": True})
    assert r.status == 404
    assert stored(w.ADA) == kept


def test_the_open_tabs_hear_an_account_removed_and_how_much_of_its_mail_went(app, world):
    tab = Stream(app)
    tab.frame()
    kept = stored(w.ADA)["messages"]
    app.post("/api/accounts/delete", {"email": w.ADA, "delete_mail": True})
    app.post("/api/accounts/delete", {"email": w.BEA})
    assert heard_until_mute(tab, app, world) == [("reconcile", {"account": w.ADA, "pruned": kept, "removed": True}),
                                                 ("reconcile", {"account": w.BEA, "pruned": 0, "removed": True})]
    tab.close()


def test_an_account_removed_while_its_sync_reads_keeps_nothing_that_sync_read(app, world):
    world.server.deliver(w.ADA, "INBOX", MARKET)
    world.server.hold("UID FETCH", user=w.ADA, when=lambda args: "BODY.PEEK[]" in str(args))
    tab = Stream(app)
    tab.frame()
    app.post("/api/refresh")
    assert world.server.held()               # Ada's sync has the new message's headers, and waits for its body
    r = app.post("/api/accounts/delete", {"email": w.ADA, "delete_mail": True})
    assert r.status == 200
    world.server.let_go()
    app.settle()
    assert stored(w.ADA) == {"messages": 0, "message_identity": 0, "sync_state": 0}
    heard = heard_until_mute(tab, app, world)
    assert ("reconcile", {"account": w.ADA, "pruned": r.json["mail_deleted"], "removed": True}) in heard
    assert [data for event, data in heard if data.get("account") == w.ADA and event != "reconcile"] == []
    assert not any(event == "new-mail" for event, _ in heard)
    tab.close()
