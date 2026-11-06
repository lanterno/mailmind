"""What a queued send is waiting for."""
from datetime import timedelta
import time

from fixtures import world as w
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
                                        "in_reply_to_pk": None, "envelope": "[]", "recipients": "{}", "subject": key,
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
