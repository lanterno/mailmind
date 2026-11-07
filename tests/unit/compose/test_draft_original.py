"""A saved reply names its original in its own bytes, by In-Reply-To."""
from datetime import timedelta

from fixtures import world as w
from mailmind.bootstrap.compose import outbox
from mailmind.bootstrap.mailbox import store

LUNCH = "ada-inbox-02-lunch-confirm.eml"
OTHER = "ada-inbox-03-newsletter.eml"
SUBJECT = "Re: Lunch on Thursday?"


def save(app, world, **kw):
    r = app.post("/api/draft", {"account": w.ADA, "mode": "reply", "in_reply_to_pk": world.pk(LUNCH), "to": ["lin@example.test"],
                                "subject": SUBJECT, "text": "See you.", "idempotency_key": "cmp-1", **kw})
    assert r.status == 200 and r.json["ok"], r.json
    return r.json


def row(did):
    c = store.conn()
    try:
        return dict(c.execute("SELECT * FROM drafts WHERE id=?", (did,)).fetchone())
    finally:
        c.close()


def in_the_list(app):
    [item] = [i for i in app.get(f"/api/messages?account={w.ADA}&folder=Drafts").json["items"] if i["subject"] == SUBJECT]
    return item["pk"]


def test_a_reopened_reply_names_its_original(app, world):
    save(app, world)
    pk = in_the_list(app)
    opened = app.post("/api/draft/resume", {"pk": pk}).json
    assert opened["orig_pk"] == world.pk(LUNCH)
    assert "lin@example.test" in opened["participants"]
    assert world.row(OTHER)["from_addr"] not in opened["participants"]


def test_a_reopened_reply_answers_its_original(app, world):
    r = save(app, world)
    pk = in_the_list(app)
    opened = app.post("/api/draft/resume", {"pk": pk}).json
    sent = app.post("/api/send", {"account": w.ADA, "mode": "reply", "in_reply_to_pk": opened["orig_pk"], "draft_id": r["draft_id"],
                                  "to": opened["to"], "subject": SUBJECT, "text": opened["text"], "idempotency_key": "cmp-1",
                                  "resume": {"in_reply_to": opened["in_reply_to"], "references": opened["references"],
                                             "tail_text": opened["tail_text"]}}).json
    assert sent["ok"], sent
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()
    [(_, _, _, flags)] = world.where(LUNCH)["server"]
    [(_, _, _, others)] = world.where(OTHER)["server"]
    assert "\\Answered" in flags and "\\Answered" not in others


def test_a_discard_leaves_what_the_row_says_of_the_composition(app, world):
    r = save(app, world)
    did = r["draft_id"]
    before = row(did)
    world.server.drop("SELECT", user=w.ADA)
    assert app.post("/api/draft", {"delete": True, "draft_id": did}).json["deleted"] is True
    after = row(did)
    assert after["version"] == before["version"] + 1
    assert {k: after[k] for k in ("mode", "subject", "saved_by")} == {k: before[k] for k in ("mode", "subject", "saved_by")}


def test_a_save_kept_apart_finds_its_original_by_its_bytes(app, world):
    r = save(app, world)
    pk = in_the_list(app)
    did = r["draft_id"]
    save(app, world, autosave=True, draft_id=did, text="Mine, not pushed yet.", resume=r["resume"])
    with world.server.lock:
        raw = world.server.mailbox(w.ADA, "Drafts").messages.pop(row(did)["uid"]).raw
    world.server.deliver(w.ADA, "Drafts", raw.replace(b"See you.", b"From another app."), flags=["\\Draft"])
    assert app.post("/api/draft/resume", {"pk": pk}).status == 409   # the list hasn't heard yet
    assert "From another app." in app.post("/api/draft/resume", {"pk": in_the_list(app)}).json["text"]
    c = store.conn()
    try:
        [apart] = [dict(x) for x in c.execute("SELECT * FROM drafts WHERE id<>?", (did,)).fetchall()]
    finally:
        c.close()
    assert (apart["mode"], apart["subject"]) == ("reply", SUBJECT)
    opened = app.post("/api/draft/resume", {"draft_id": apart["id"]}).json
    assert "Mine, not pushed yet." in opened["text"] and opened["orig_pk"] == world.pk(LUNCH)
