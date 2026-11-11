"""The outbox: a crash after delivery, the Sent copy a send still owes, how a send is listed, its Undo window and a retry."""
import threading
import time
from datetime import timedelta

import problems
from fixtures import world as w
from mailmind.bootstrap import compose
from mailmind.compose.domain import outbox_item
from stored_rows import q
from waiting import wait_until
from world_helpers import _Stop, new


def outbox_row(oid):
    [r] = q("SELECT state, attempts, delivered, sent_appended, message_id, raw FROM outbox WHERE id=?", oid)
    return r


def test_hwf_a_crash_after_delivery_never_sends_twice(app, world, monkeypatch):
    from mailmind.bootstrap.compose import outbox
    from mailmind.bootstrap.mailbox import store
    oid = app.post("/api/send", new(idempotency_key="k-crash")).json["outbox_id"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    real = outbox._side_effects

    def dies(*a):
        raise RuntimeError("the process died here")
    monkeypatch.setattr(outbox, "_side_effects", dies)
    outbox.drain()
    assert (outbox_row(oid)["state"], outbox_row(oid)["delivered"], len(world.smtp.messages)) == ("sending", 1, 1)
    monkeypatch.setattr(outbox, "_side_effects", real)
    c = store.conn()
    outbox.QUEUE.reset_stuck(c)                                  # what the worker does at boot
    c.commit()
    c.close()
    outbox.drain()
    r = outbox_row(oid)
    assert (r["state"], r["sent_appended"], len(world.smtp.messages)) == ("sent", 1, 1)
    assert len(world.server.find(w.ADA, "Sent", r["message_id"])) == 1


def test_hwf_the_sent_copy_is_the_bytes_that_went_on_the_wire(app, world):
    from mailmind.bootstrap.compose import outbox
    oid = app.post("/api/send", new(idempotency_key="k-bytes")).json["outbox_id"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()
    r = outbox_row(oid)
    [wire] = world.smtp.messages
    [uid] = world.server.find(w.ADA, "Sent", r["message_id"])
    copy = world.server.messages(w.ADA, "Sent")[uid].raw
    assert copy == r["raw"]
    assert copy.rstrip(b"\r\n") == wire.data.rstrip(b"\r\n")


def test_hwf_a_sent_copy_that_failed_is_filed_once_the_server_is_back(app, world):
    from mailmind.bootstrap.compose import outbox
    oid = app.post("/api/send", new(idempotency_key="k-sent-later")).json["outbox_id"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    world.server.drop("APPEND", user=w.ADA)                     # the connection goes as the copy goes up
    outbox.drain()
    assert len(world.smtp.messages) == 1                         # delivered all the same
    app.clock.shift(timedelta(minutes=20))                       # past any back-off
    outbox.drain()
    r = outbox_row(oid)
    assert (r["sent_appended"], len(world.smtp.messages)) == (1, 1)
    assert len(world.server.find(w.ADA, "Sent", r["message_id"])) == 1


def test_hwf_a_sent_copy_that_landed_but_lost_its_answer_is_filed_once(app, world):
    from mailmind.bootstrap.compose import outbox
    oid = app.post("/api/send", new(idempotency_key="k-landed")).json["outbox_id"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    world.server.drop_after("APPEND", user=w.ADA)               # the copy is filed; its OK never comes
    outbox.drain()
    assert outbox_row(oid)["sent_appended"] == 0
    app.clock.shift(timedelta(minutes=20))
    outbox.drain()
    r = outbox_row(oid)
    assert (r["state"], r["sent_appended"], len(world.smtp.messages)) == ("sent", 1, 1)
    assert len(world.server.find(w.ADA, "Sent", r["message_id"])) == 1


def test_hwf_a_send_owing_its_sent_copy_is_listed_sent_and_retried_once_it_gives_up(app, world):
    from mailmind.bootstrap.compose import outbox

    def listed():
        return {i["id"]: i for i in app.get("/api/outbox").json["items"]}[oid]
    oid = app.post("/api/send", new(idempotency_key="k-owed")).json["outbox_id"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    world.server.fail("APPEND", times=outbox.MAX_ATTEMPTS, user=w.ADA)
    outbox.drain()
    item = listed()
    assert (item["state"], item["scheduled"], item["error"]) == ("sent", False, outbox_item.SENT_COPY_PENDING)
    assert app.post("/api/outbox/cancel", {"id": oid}).json == problems.conflict("too late to cancel")   # it went out
    for _ in range(outbox.MAX_ATTEMPTS - 1):
        app.clock.shift(timedelta(seconds=outbox._BACKOFF_CAP))
        outbox.drain()
    item = listed()
    assert (item["state"], item["error"]) == ("error", outbox_item.SENT_COPY_FAILED)
    assert len(world.server.commands_for(w.ADA, "APPEND")) == outbox.MAX_ATTEMPTS
    assert app.post("/api/outbox/retry", {"id": oid}).json == {"ok": True}
    outbox.drain()
    r = outbox_row(oid)
    assert (r["state"], r["sent_appended"], len(world.smtp.messages)) == ("sent", 1, 1)
    assert len(world.server.find(w.ADA, "Sent", r["message_id"])) == 1
    assert (listed()["state"], listed()["error"]) == ("sent", None)


def test_hwf_only_a_send_past_the_undo_window_is_listed_as_scheduled(app):
    from mailmind.bootstrap.compose import outbox
    now = int(time.time())                                       # frozen
    ids = [app.post("/api/send", new(idempotency_key=f"k-{n}", **kw)).json["outbox_id"] for n, kw in enumerate((
        {}, {"send_after": now + outbox.SEND_DELAY}, {"send_after": now + outbox.SEND_DELAY + 1}))]
    listed = {i["id"]: i["scheduled"] for i in app.get("/api/outbox").json["items"]}
    assert [listed[i] for i in ids] == [False, False, True]


class _StoppableDrain:
    """The outbox worker's drain: drains as before until stop(), then ends the worker's loop."""

    def __init__(self, drain):
        self.drain, self.stopping = drain, False

    def __call__(self):
        if self.stopping:
            raise _Stop()
        return self.drain()

    def stop(self):
        from mailmind.bootstrap.compose import outbox
        self.stopping = True
        outbox.kick()


def test_hwf_a_send_goes_out_when_its_undo_window_ends(world, monkeypatch):
    from mailmind.bootstrap.compose import outbox
    monkeypatch.setattr(outbox, "SEND_DELAY", 1)                 # a one-second Undo Send window, in real time
    drain = _StoppableDrain(outbox.drain)
    monkeypatch.setattr(outbox, "drain", drain)

    def worker():
        try:
            outbox.worker_loop()
        except _Stop:
            pass
    t = threading.Thread(target=worker, name="outbox-worker", daemon=True)
    t.start()
    try:
        assert compose.sending.send(new(idempotency_key="k-soon"))["ok"]
        wait_until(lambda: world.smtp.messages, timeout=15)      # due within a second; the worker polls every 30
        assert world.smtp.messages
    finally:
        drain.stop()
        t.join(5)
        for lane in threading.enumerate():                       # or a late send lands in the next test's database
            if lane.name.startswith("outbox:"):
                lane.join(15)
    assert not t.is_alive()


def test_hwf_a_manual_retry_gets_a_fresh_set_of_attempts(app, world):
    from mailmind.bootstrap.compose import outbox
    world.smtp.fail("RCPT", "451 4.3.0 try again later", times=None)
    oid = app.post("/api/send", new(idempotency_key="k-retry")).json["outbox_id"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    for _ in range(outbox.MAX_ATTEMPTS):
        outbox.drain()
        app.clock.shift(timedelta(seconds=outbox._BACKOFF_CAP + 1))
    assert outbox_row(oid)["state"] == "error"
    assert app.post("/api/outbox/retry", {"id": oid}).json["ok"]
    outbox.drain()                                               # the retry fails once more
    r = outbox_row(oid)
    assert (r["state"], r["attempts"]) == ("queued", 1)
