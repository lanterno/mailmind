"""What a queued send is waiting for, what's still owed once it has gone out, and a Retry that finds it went."""
from datetime import timedelta
import time

from fixtures import world as w
from gate import held_elsewhere
from mailmind.platform.db.migrations import m0041_outbox_wait_kind


def new(key="k-1", account=w.ADA, **kw):
    return {"account": account, "to": ["lin@example.test"], "subject": "Thread colours", "text": "Which blue?",
            "idempotency_key": key, **kw}


def row(oid):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return dict(c.execute("SELECT * FROM outbox WHERE id=?", (oid,)).fetchone())
    finally:
        c.close()


def listed(app, oid):
    return next(o for o in app.get("/api/outbox").json["items"] if o["id"] == oid)


def out(app):
    """Undo Send's window passes, and what's queued goes."""
    from mailmind.bootstrap.compose import outbox
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()


def later(app):
    from mailmind.bootstrap.compose import outbox
    app.clock.shift(timedelta(seconds=outbox._BACKOFF_CAP + 1))
    outbox.drain()


def waits(item):
    return item["scheduled"], item["retrying"]


# -- what a queued send is waiting for -----------------------------------------

def test_a_send_in_its_undo_window_is_neither(app):
    oid = app.post("/api/send", new()).json["outbox_id"]
    assert waits(listed(app, oid)) == (False, False)


def test_a_time_someone_picked_is_scheduled_however_soon_it_comes(app):
    from mailmind.bootstrap.compose import outbox
    oid = app.post("/api/send", new(send_after=int(time.time()) + outbox.SEND_DELAY + 1)).json["outbox_id"]
    assert waits(listed(app, oid)) == (True, False)


def test_a_send_that_failed_is_retrying_not_scheduled(app, world):
    oid = app.post("/api/send", new()).json["outbox_id"]
    world.smtp.fail("MAIL")
    out(app)
    item = listed(app, oid)
    assert (item["state"], waits(item)) == ("queued", (False, True))
    assert item["error"]


def test_a_scheduled_send_that_failed_when_its_time_came_is_retrying(app, world):
    oid = app.post("/api/send", new(send_after=int(time.time()) + 3600)).json["outbox_id"]
    app.clock.shift(timedelta(hours=1, seconds=1))
    world.smtp.fail("MAIL")
    from mailmind.bootstrap.compose import outbox
    outbox.drain()
    assert waits(listed(app, oid)) == (False, True)


def test_try_now_sends_a_retrying_send_and_it_stops_being_one(app, world):
    from mailmind.bootstrap.compose import outbox
    oid = app.post("/api/send", new()).json["outbox_id"]
    world.smtp.fail("MAIL")
    out(app)
    assert app.post("/api/outbox/sendnow", {"id": oid}).json["ok"]
    assert waits(listed(app, oid)) == (False, False)
    outbox.drain()
    assert (listed(app, oid)["state"], len(world.smtp.messages)) == ("sent", 1)


def test_a_retrying_send_can_be_cancelled(app, world):
    oid = app.post("/api/send", new()).json["outbox_id"]
    world.smtp.fail("MAIL")
    out(app)
    assert app.post("/api/outbox/cancel", {"id": oid}).json["ok"]
    later(app)
    assert world.smtp.messages == []
    assert [o for o in app.get("/api/outbox").json["items"] if o["id"] == oid] == []


def test_a_persons_retry_of_a_failed_send_waits_for_nothing(app, world):
    oid = app.post("/api/send", new()).json["outbox_id"]
    world.smtp.fail("MAIL", "550 5.7.1 not from here")
    out(app)
    assert listed(app, oid)["state"] == "error"
    assert app.post("/api/outbox/retry", {"id": oid}).json == {"ok": True}
    item = listed(app, oid)
    assert (item["state"], waits(item)) == ("queued", (False, False))


def test_the_upgrade_reads_what_is_already_queued(data_dir):
    from mailmind.bootstrap.compose import book
    from mailmind.bootstrap.mailbox import store
    now = int(time.time())
    c = store.conn()

    def queued(key, due, attempts=0, state="queued"):
        oid, _ = book.outbox_insert(c, {"account": w.ADA, "idempotency_key": key, "message_id": f"<{key}@example.test>",
                                        "envelope": "[]", "recipients": "{}", "subject": key,
                                        "raw": b"x", "next_attempt_at": due, "draft_id": None,
                                        "created_at": "2026-11-02T12:00:00+00:00"})
        c.execute("UPDATE outbox SET attempts=?, state=? WHERE id=?", (attempts, state, oid))
        return oid
    in_window, scheduled = queued("window", now + 10), queued("picked", now + 3600)
    backing_off, done = queued("failed", now + 3600, attempts=2), queued("done", now + 3600, state="sent")
    for _ in range(2):                                         # init runs on every start
        m0041_outbox_wait_kind.migrate(c)
    kinds = {r["id"]: r["wait_kind"] for r in c.execute("SELECT id, wait_kind FROM outbox")}
    c.close()
    assert kinds == {in_window: "grace", scheduled: "scheduled", backing_off: "backoff", done: "grace"}


# -- what it still owes once it has gone out --------------------------------------

def reply(app, world):
    pk = world.pk("ada-inbox-03-newsletter.eml")
    return app.post("/api/send", new(mode="reply", in_reply_to_pk=pk)).json["outbox_id"]


def test_a_reply_whose_answered_mark_failed_gets_it_on_a_later_try(app, world):
    oid = reply(app, world)
    world.server.drop("UID STORE", user=w.ADA)
    out(app)
    assert (row(oid)["answered_set"], row(oid)["delivered"], listed(app, oid)["state"]) == (0, 1, "sent")
    assert listed(app, oid)["error"] is None                   # nothing a person need do
    later(app)
    assert (row(oid)["answered_set"], row(oid)["state"]) == (1, "sent")
    assert len(world.smtp.messages) == 1


def test_a_mark_the_server_refuses_isnt_asked_for_again(app, world):
    oid = reply(app, world)
    world.server.fail("UID STORE", "NO [CANNOT] not today", user=w.ADA)
    out(app)
    assert row(oid)["state"] == "sent"


def test_a_draft_copy_that_wouldnt_go_goes_on_a_later_try(app, world, monkeypatch):
    from mailmind.bootstrap.compose import outbox
    saved = app.post("/api/draft", new(subject="Held")).json
    oid = app.post("/api/send", new("k-2", subject="Held", draft_id=saved["draft_id"])).json["outbox_id"]

    def refuses(*a, **kw):
        raise OSError("gone away")
    with monkeypatch.context() as m:
        m.setattr(outbox._copies, "delete_draft_uid", refuses)
        out(app)
    assert (row(oid)["draft_deleted"], row(oid)["state"], listed(app, oid)["state"]) == (0, "queued", "sent")
    later(app)
    assert (row(oid)["draft_deleted"], row(oid)["state"]) == (1, "sent")
    assert len(world.smtp.messages) == 1


def test_what_gives_up_after_going_out_leaves_it_listed_as_sent(app, world):
    oid = reply(app, world)
    world.server.drop("UID STORE", user=w.ADA, times=50)
    out(app)
    for _ in range(6):
        later(app)
    item = listed(app, oid)
    assert row(oid)["state"] == "error"
    assert (item["state"], item["error"], item["unsure"], waits(item)) == ("sent", None, False, (False, False))
    assert len(world.smtp.messages) == 1


# -- Retry on a send that went unanswered -----------------------------------------

def unanswered(app, world, account):
    oid = app.post("/api/send", new(account=account)).json["outbox_id"]
    world.smtp.drop_after_data()
    out(app)
    assert listed(app, oid)["unsure"]
    return oid


def test_a_copy_in_gmails_sent_says_it_went_and_it_isnt_sent_again(app, world):
    from mailmind.bootstrap.compose import outbox
    oid = unanswered(app, world, w.CY)
    assert app.post("/api/outbox/retry", {"id": oid}).json == {"ok": True, "gone": True}
    outbox.drain()
    item = listed(app, oid)
    assert (item["state"], item["error"], item["unsure"]) == ("sent", None, False)
    assert len(world.smtp.messages) == 1


def test_no_copy_in_gmails_sent_sends_it_again_as_they_were_told(app, world, monkeypatch):
    from mailmind.bootstrap.compose import outbox
    oid = unanswered(app, world, w.CY)
    monkeypatch.setattr(outbox._copies, "in_sent", lambda acct, message_id: False)
    assert app.post("/api/outbox/retry", {"id": oid}).json == {"ok": True}
    outbox.drain()
    assert (listed(app, oid)["state"], len(world.smtp.messages)) == ("sent", 2)


def test_a_look_in_sent_that_fails_sends_it_again_as_they_were_told(app, world):
    from mailmind.bootstrap.compose import outbox
    oid = unanswered(app, world, w.CY)
    world.server.drop("EXAMINE", user=w.CY)
    assert app.post("/api/outbox/retry", {"id": oid}).json == {"ok": True}
    outbox.drain()
    assert len(world.smtp.messages) == 2


def test_a_busy_gate_doesnt_keep_the_click_waiting(app, world, monkeypatch):
    from mailmind.bootstrap.compose import outbox
    oid = unanswered(app, world, w.CY)
    monkeypatch.setattr(outbox, "LOOK_IN_SENT_WAIT", 0.01)
    with held_elsewhere(w.CY):
        assert app.post("/api/outbox/retry", {"id": oid}).json == {"ok": True}


def test_only_a_server_that_files_sent_mail_itself_is_asked(app, world, monkeypatch):
    from mailmind.bootstrap.compose import outbox
    oid = unanswered(app, world, w.ADA)

    def asked(*a):
        raise AssertionError("MailMind files Ada's Sent copies, so none can be there")
    monkeypatch.setattr(outbox._copies, "in_sent", asked)
    assert app.post("/api/outbox/retry", {"id": oid}).json == {"ok": True}


def test_a_retry_of_a_send_that_was_refused_doesnt_look_either(app, world, monkeypatch):
    from mailmind.bootstrap.compose import outbox
    oid = app.post("/api/send", new(account=w.CY)).json["outbox_id"]
    world.smtp.fail("MAIL", "550 5.7.1 not from here")
    out(app)

    def asked(*a):
        raise AssertionError("a refused send never reached the far side")
    monkeypatch.setattr(outbox._copies, "in_sent", asked)
    assert app.post("/api/outbox/retry", {"id": oid}).json == {"ok": True}
