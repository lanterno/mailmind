"""A draft is local first (decision D5). A save lands in the journal and answers at once; the server's copy follows it on
close, on an explicit save and at most once a minute while someone types, and a server that's away costs nothing but
waiting. The journal holds one row a draft, the newest save, and the copy a try may have left never stays behind."""
import time
from datetime import timedelta

import gate
from fixtures import world as w
from mailmind.bootstrap import mailbox
from mailmind.bootstrap.compose import book
from mailmind.bootstrap import compose
from mailmind.compose.domain.draft_journal import PUSH_EVERY
from mailmind.bootstrap.mailbox import store


def new(**kw):
    return {"account": w.ADA, "mode": "new", "to": ["lin@example.test"], "subject": "Swatch order", "text": "Two of each.",
            "idempotency_key": "cmp-1", **kw}


def save(app, **kw):
    r = app.post("/api/draft", new(**kw))
    assert r.status == 200 and r.json["ok"], r.json
    return r.json


def live_copies(world, account=w.ADA):
    """Drafts on the server, as the text each holds."""
    with world.server.lock:
        return [m.raw for m in world.server.mailbox(account, "Drafts").messages.values()
                if "\\Deleted" not in m.flags and b"Swatch order" in m.raw]


def holds(world, *texts):
    copies = live_copies(world)
    return len(copies) == len(texts) and all(t.encode() in c for t, c in zip(texts, copies))


def row(did):
    c = store.conn()
    try:
        return c.execute("SELECT * FROM drafts WHERE id=?", (did,)).fetchone()
    finally:
        c.close()


def waiting(did):
    c = store.conn()
    try:
        return book.journal_get(c, did)
    finally:
        c.close()


def a_minute_on(app):
    app.clock.shift(timedelta(seconds=PUSH_EVERY + 1))


def tray(app):
    return app.get("/api/outbox").json["drafts"]


# -- a save and its push ----------------------------------------------------------------

def test_an_autosave_touches_no_server(app, world):
    since = world.mark()
    r = save(app, autosave=True)
    assert r["pushed"] is False and r["version"] == 1
    assert world.writes(since) == []
    assert b"Two of each." in waiting(r["draft_id"])["raw"]


def test_autosaves_make_one_push_a_minute_of_the_newest_text(app, world):
    did = save(app, autosave=True, text="One.")["draft_id"]
    assert save(app, autosave=True, draft_id=did, text="Two.")["version"] == 2
    compose.drafts.push_round()
    assert live_copies(world) == []
    a_minute_on(app)
    compose.drafts.push_round()
    assert holds(world, "Two.") and waiting(did) is None
    save(app, autosave=True, draft_id=did, text="Three.")
    compose.drafts.push_round()                                    # the last push was only now
    assert holds(world, "Two.")
    a_minute_on(app)
    compose.drafts.push_round()
    assert holds(world, "Three.") and waiting(did) is None


def test_a_save_someone_asked_for_goes_now_and_takes_what_autosave_left(app, world):
    did = save(app, autosave=True, text="One.")["draft_id"]
    r = save(app, draft_id=did, text="Two.")
    assert r["pushed"] is True and row(did)["uid"] == r["uid"]
    assert holds(world, "Two.") and waiting(did) is None


def test_a_busy_gate_leaves_a_save_to_the_job(app, world, monkeypatch):
    monkeypatch.setattr("mailmind.compose.application.drafts.PUSH_WAIT", 0.01)
    with gate.held_elsewhere(w.ADA):
        r = save(app)
    assert r["pushed"] is False and live_copies(world) == []
    compose.drafts.push_round()
    assert holds(world, "Two of each.")


def test_a_server_that_is_away_only_makes_a_save_wait(app, world):
    world.server.drop("APPEND", user=w.ADA)
    r = save(app)
    assert r["pushed"] is False and waiting(r["draft_id"])["attempts"] == 1
    assert [(d["id"], d["attempts"], d["discarding"]) for d in tray(app)] == [(r["draft_id"], 1, False)]
    a_minute_on(app)
    compose.drafts.push_round()
    assert holds(world, "Two of each.") and tray(app) == [] and waiting(r["draft_id"]) is None


def test_a_refusing_server_is_asked_again_later_and_later(app, world):
    did = save(app, autosave=True)["draft_id"]
    a_minute_on(app)
    world.server.fail("APPEND", user=w.ADA, times=2)
    compose.drafts.push_round()
    first = waiting(did)
    assert first["attempts"] == 1
    wait = first["due_at"] - int(time.time())
    app.clock.shift(timedelta(seconds=wait + 1))
    compose.drafts.push_round()
    second = waiting(did)
    assert second["attempts"] == 2 and second["due_at"] - int(time.time()) > wait
    app.clock.shift(timedelta(minutes=2))
    compose.drafts.push_round()
    assert holds(world, "Two of each.")


def test_a_save_made_while_a_push_is_on_its_way_stays_to_go(app, world, monkeypatch):
    did = save(app, autosave=True, text="One.")["draft_id"]
    a_minute_on(app)
    real = mailbox.copies.append_to_drafts

    def append(*args, **kw):
        save(app, autosave=True, draft_id=did, text="Two.")
        return real(*args, **kw)
    monkeypatch.setattr(mailbox.copies, "append_to_drafts", append)
    compose.drafts.push_round()
    assert holds(world, "One.") and row(did)["uid"] is not None
    left = waiting(did)
    assert left["version"] == 2 and left["tried"] is None
    monkeypatch.undo()
    a_minute_on(app)
    compose.drafts.push_round()
    assert holds(world, "Two.") and waiting(did) is None


def test_a_reply_keeps_its_quote_formatted_before_any_push(app, world):
    reply = {"mode": "reply", "original_key": world.key("ada-inbox-03-newsletter.eml"), "autosave": True}
    first = save(app, **reply)
    assert b"text/html" in waiting(first["draft_id"])["raw"]
    second = save(app, mode="reply", resume=first["resume"], draft_id=first["draft_id"], autosave=True, text="Yes, Friday.")
    assert b"text/html" in waiting(second["draft_id"])["raw"]


# -- a try that may have reached the server --------------------------------------------

def test_a_push_that_never_heard_its_answer_leaves_one_copy(app, world):
    world.server.drop_after("APPEND", user=w.ADA)
    r = save(app)
    assert r["pushed"] is False and len(live_copies(world)) == 1 and row(r["draft_id"])["uid"] is None
    a_minute_on(app)
    compose.drafts.push_round()
    assert holds(world, "Two of each.") and waiting(r["draft_id"]) is None
    assert row(r["draft_id"])["uid"] is not None


def test_a_newer_save_replaces_the_copy_such_a_push_left(app, world):
    world.server.drop_after("APPEND", user=w.ADA)
    did = save(app, text="One.")["draft_id"]
    assert save(app, draft_id=did, text="Two.")["pushed"] is True
    assert holds(world, "Two.") and waiting(did) is None


def test_a_crash_before_a_push_settles_goes_on_after_the_restart(app, world, monkeypatch):
    did = save(app, autosave=True)["draft_id"]
    a_minute_on(app)

    class Crash(BaseException):
        pass

    def crash(*a, **kw):
        raise Crash
    real = book.draft_settle_push
    monkeypatch.setattr(book, "draft_settle_push", crash)
    try:
        compose.drafts.push_round()
    except Crash:
        pass
    monkeypatch.setattr(book, "draft_settle_push", real)
    assert waiting(did)["tried"] is not None and len(live_copies(world)) == 1
    compose.drafts.push_round()
    assert holds(world, "Two of each.") and waiting(did) is None


# -- two windows ---------------------------------------------------------------------------

def test_a_save_that_never_saw_the_last_one_is_refused(app, world):
    did = save(app, idempotency_key="tab-1")["draft_id"]
    assert save(app, idempotency_key="tab-2", draft_id=did, base_version=1, text="From tab two.")["version"] == 2
    r = app.post("/api/draft", new(idempotency_key="tab-1", draft_id=did, base_version=1, text="From tab one."))
    assert r.status == 409 and r.json["conflict"] is True and r.json["version"] == 2
    assert holds(world, "From tab two.")
    assert save(app, idempotency_key="tab-1", draft_id=did, text="From tab one.")["version"] == 3   # asked for by name


def test_a_save_that_names_no_composer_is_held_to_its_version(app, world):
    did = save(app)["draft_id"]
    body = new(draft_id=did, base_version=0)
    del body["idempotency_key"]
    assert app.post("/api/draft", body).status == 409


def test_a_composer_whose_answer_was_lost_is_not_its_own_conflict(app, world):
    did = save(app, autosave=True)["draft_id"]
    save(app, autosave=True, draft_id=did, base_version=1, text="One.")
    assert save(app, autosave=True, draft_id=did, base_version=1, text="Two.")["version"] == 3


# -- reopening --------------------------------------------------------------------------------

def opened_from_the_list(app, r):
    """The draft’s copy as the list names it: by its key, folder and uid."""
    [item] = [i for i in app.get(f"/api/messages?account={w.ADA}&folder=Drafts").json["items"]
              if i["subject"] == "Swatch order"]
    return {"key": item["key"], "folder": item["folder"], "uid": item["uid"]}


def test_reopening_a_copy_prefers_the_newer_save(app, world):
    r = save(app, bcc=["cy@example.test"])
    copy = opened_from_the_list(app, r)
    save(app, autosave=True, draft_id=r["draft_id"], text="Three of each.", bcc=["dee@example.test"])
    opened = app.post("/api/draft/resume", copy).json
    assert opened["text"] == "Three of each." and opened["version"] == 2
    assert [b for b in opened["bcc"]] == ["dee@example.test"]


def test_a_copy_another_app_changed_and_a_waiting_save_both_stay(app, world):
    r = save(app)
    copy = opened_from_the_list(app, r)
    did = r["draft_id"]
    save(app, autosave=True, draft_id=did, text="Mine, not pushed yet.")
    with world.server.lock:
        box = world.server.mailbox(w.ADA, "Drafts")
        raw = box.messages.pop(row(did)["uid"]).raw
    world.server.deliver(w.ADA, "Drafts", raw.replace(b"Two of each.", b"From another app."), flags=["\\Draft"])
    assert app.post("/api/draft/resume", copy).status == 409   # the list hasn't heard yet
    fresh = opened_from_the_list(app, r)
    opened = app.post("/api/draft/resume", fresh).json
    assert "From another app." in opened["text"] and waiting(did) is None
    a_minute_on(app)
    compose.drafts.push_round()
    texts = sorted(b"another" in c for c in live_copies(world))
    assert texts == [False, True] and any(b"Mine, not pushed yet." in c for c in live_copies(world))


def test_a_draft_only_the_journal_holds_opens_from_the_tray(app, world):
    world.server.drop("APPEND", user=w.ADA)
    r = save(app, bcc=["cy@example.test"])
    opened = app.post("/api/draft/resume", {"draft_id": r["draft_id"]}).json
    assert (opened["text"], opened["bcc"], opened["version"], opened["account"]) == (
        "Two of each.", ["cy@example.test"], 1, w.ADA)
    a_minute_on(app)
    compose.drafts.push_round()
    assert app.post("/api/draft/resume", {"draft_id": r["draft_id"]}).status == 409    # it's in Drafts now


# -- discarding --------------------------------------------------------------------------------

def test_discarding_what_never_reached_the_server_asks_nothing_of_it(app, world):
    did = save(app, autosave=True)["draft_id"]
    since = world.mark()
    assert app.post("/api/draft", {"delete": True, "draft_id": did}).json["deleted"] is True
    assert world.writes(since) == [] and row(did) is None and waiting(did) is None


def test_a_discard_outlasts_a_server_that_is_away(app, world):
    r = save(app)
    copy = opened_from_the_list(app, r)
    did = r["draft_id"]
    world.server.drop("SELECT", user=w.ADA)
    assert app.post("/api/draft", {"delete": True, "draft_id": did}).json["deleted"] is True
    assert row(did) is not None and len(live_copies(world)) == 1
    assert [d["discarding"] for d in tray(app)] == [True]
    assert app.post("/api/draft/resume", copy).status == 409
    a_minute_on(app)
    compose.drafts.push_round()
    assert row(did) is None and live_copies(world) == [] and tray(app) == []


def test_a_discard_takes_the_copy_a_try_may_have_left(app, world):
    world.server.drop_after("APPEND", user=w.ADA)
    did = save(app)["draft_id"]
    assert len(live_copies(world)) == 1
    assert app.post("/api/draft", {"delete": True, "draft_id": did}).json["deleted"] is True
    assert row(did) is None and live_copies(world) == []


def test_a_save_after_a_discard_is_a_draft_of_its_own(app, world):
    r = save(app)
    world.server.drop("SELECT", user=w.ADA, times=2)
    app.post("/api/draft", {"delete": True, "draft_id": r["draft_id"]})
    again = save(app, draft_id=r["draft_id"], text="Changed my mind.", autosave=True)
    assert again["draft_id"] != r["draft_id"]


# -- a send --------------------------------------------------------------------------------------

def test_a_draft_only_the_journal_holds_can_be_sent(app, world):
    did = save(app, autosave=True)["draft_id"]
    assert app.post("/api/send", new(draft_id=did)).json["ok"]
    app.clock.shift(timedelta(seconds=compose.outbox.SEND_DELAY + 1))
    compose.outbox.drain()
    assert len(world.smtp.messages) == 1
    assert row(did) is None and waiting(did) is None and live_copies(world) == []


def test_a_send_takes_the_copy_a_try_may_have_left(app, world):
    world.server.drop_after("APPEND", user=w.ADA)
    did = save(app)["draft_id"]
    assert len(live_copies(world)) == 1
    assert app.post("/api/send", new(draft_id=did)).json["ok"]
    app.clock.shift(timedelta(seconds=compose.outbox.SEND_DELAY + 1))
    compose.outbox.drain()
    assert len(world.smtp.messages) == 1 and row(did) is None and live_copies(world) == []


def test_the_job_looks_again_when_the_next_save_comes_due(app, world):
    from mailmind.compose.domain.draft_journal import PUSH_TICK
    assert compose.drafts.push_round() == PUSH_TICK
    did = save(app, autosave=True)["draft_id"]
    assert compose.drafts.push_round() == PUSH_TICK                # a minute off
    assert app.post("/api/send", new(draft_id=did)).json["ok"]
    a_minute_on(app)
    assert compose.drafts.push_round() == PUSH_TICK                # due, but held: nothing to look for sooner


def test_a_held_draft_refuses_a_save_and_isnt_pushed(app, world):
    did = save(app, autosave=True)["draft_id"]
    assert app.post("/api/send", new(draft_id=did)).json["ok"]
    assert app.post("/api/draft", new(draft_id=did, text="Too late.")).status == 423
    assert tray(app) == []                                 # the outbox's row says it


def test_discarding_a_draft_with_no_row_answers_as_gone(data_dir):
    assert compose.drafts.save({"delete": True, "draft_id": 12345}) == {"ok": True, "deleted": True}


def test_a_send_about_to_go_keeps_its_draft_from_being_pushed(app, world):
    did = save(app, autosave=True)["draft_id"]
    assert app.post("/api/send", new(draft_id=did)).json["ok"]
    c = store.conn()
    book.journal_failed(c, did, 0, 0)                      # as good as due
    c.commit()
    c.close()
    compose.drafts.push_round()
    assert live_copies(world) == []


def test_a_send_scheduled_for_later_lets_its_draft_catch_up(app, world):
    did = save(app, autosave=True)["draft_id"]
    later = int(time.time()) + 7200
    assert app.post("/api/send", new(draft_id=did, send_after=later)).json["ok"]
    a_minute_on(app)
    compose.drafts.push_round()
    assert holds(world, "Two of each.")
    app.clock.shift(timedelta(hours=3))
    compose.outbox.drain()
    assert len(world.smtp.messages) == 1 and live_copies(world) == [] and row(did) is None


def test_undo_send_leaves_the_journal_to_go_on(app, world):
    did = save(app, autosave=True)["draft_id"]
    sent = app.post("/api/send", new(draft_id=did)).json
    assert app.post("/api/outbox/cancel", {"id": sent["outbox_id"]}).json["ok"]
    a_minute_on(app)
    compose.drafts.push_round()
    assert holds(world, "Two of each.") and waiting(did) is None
