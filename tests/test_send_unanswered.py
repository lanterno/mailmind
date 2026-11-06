"""A send's last bytes: on the near side of them a failure retries, and on the far side, with no answer
from the server, the message may have gone out, so it's never sent again without a person's say."""
from datetime import timedelta

import pytest

from fixtures import world as w
from mailmind.compose.domain import outbox_item


def new(key):
    return {"account": w.ADA, "to": ["lin@example.test"], "subject": "Thread colours", "text": "Which blue?",
            "idempotency_key": key}


def row(oid):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return dict(c.execute("SELECT * FROM outbox WHERE id=?", (oid,)).fetchone())
    finally:
        c.close()


def queued(app, key="k-1"):
    from mailmind.bootstrap.compose import outbox
    oid = app.post("/api/send", new(key)).json["outbox_id"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    return oid


def listed(app, oid):
    return next(o for o in app.get("/api/outbox").json["items"] if o["id"] == oid)


def later(app):
    from mailmind.bootstrap.compose import outbox
    app.clock.shift(timedelta(seconds=outbox._BACKOFF_CAP + 1))
    outbox.drain()


@pytest.mark.parametrize("command", ["MAIL", "RCPT", "DATA"])
def test_a_connection_lost_before_the_message_went_is_tried_again(app, world, command):
    from mailmind.bootstrap.compose import outbox
    oid = queued(app)
    world.smtp.drop(command)
    outbox.drain()
    r = row(oid)
    assert (r["state"], r["wire_begun"], r["attempts"]) == ("queued", 0, 1)
    assert world.smtp.messages == []
    later(app)
    assert row(oid)["state"] == "sent" and len(world.smtp.messages) == 1


def test_a_refusal_after_the_message_went_is_an_answer_and_is_tried_again(app, world):
    from mailmind.bootstrap.compose import outbox
    oid = queued(app)
    world.smtp.fail("DATA-END", "451 4.3.0 try again later")
    outbox.drain()
    r = row(oid)
    assert (r["state"], r["wire_begun"], r["last_error"]) == ("queued", 0, "rejected by server")
    later(app)
    assert row(oid)["state"] == "sent" and len(world.smtp.messages) == 1


def test_a_refusal_for_good_after_the_message_went_gives_up_as_a_failure_not_a_doubt(app, world):
    from mailmind.bootstrap.compose import outbox
    oid = queued(app)
    world.smtp.fail("DATA-END", "554 5.7.1 refused")
    outbox.drain()
    r = listed(app, oid)
    assert (r["state"], r["error"], r["unsure"]) == ("error", "rejected by server", False)


def test_no_answer_to_the_whole_message_stops_and_says_so(app, world):
    from mailmind.bootstrap.compose import outbox
    oid = queued(app)
    world.smtp.drop_after_data()
    outbox.drain()
    assert len(world.smtp.messages) == 1
    r = listed(app, oid)
    assert (r["state"], r["error"], r["unsure"]) == ("error", outbox_item.UNSURE, True)
    later(app)
    later(app)
    assert len(world.smtp.messages) == 1                       # never again on its own


def test_a_person_can_send_it_again_and_it_finishes_like_any_send(app, world):
    from mailmind.bootstrap.compose import outbox
    oid = queued(app)
    world.smtp.drop_after_data()
    outbox.drain()
    assert app.post("/api/outbox/retry", {"id": oid}).json["ok"]
    assert row(oid)["wire_begun"] == 0
    outbox.drain()
    r = listed(app, oid)
    assert (r["state"], r["error"], r["unsure"]) == ("sent", None, False)
    assert len(world.smtp.messages) == 2


def test_a_crash_with_the_message_on_the_wire_isnt_sent_again_after_the_restart(app, world, monkeypatch):
    from mailmind.bootstrap.compose import outbox
    from mailmind.bootstrap.compose import transport
    from mailmind.bootstrap.mailbox import store
    oid = queued(app)
    real = transport.send

    class Died(BaseException):
        pass

    def dies(acct, raw, envelope, before_dot=None):
        before_dot()
        raise Died
    monkeypatch.setattr(transport, "send", dies)
    with pytest.raises(Died):
        outbox.send_one(oid)
    monkeypatch.setattr(transport, "send", real)
    assert (row(oid)["state"], row(oid)["wire_begun"]) == ("sending", 1)
    c = store.conn()
    outbox.QUEUE.reset_stuck(c)                                # what the worker does at boot
    c.commit()
    c.close()
    assert listed(app, oid)["unsure"] is True
    later(app)
    assert world.smtp.messages == []


def test_a_crash_before_the_message_went_is_tried_again_after_the_restart(app, world, monkeypatch):
    from mailmind.bootstrap.compose import outbox
    from mailmind.bootstrap.compose import transport
    from mailmind.bootstrap.mailbox import store
    oid = queued(app)
    real = transport.send

    class Died(BaseException):
        pass

    def dies(acct, raw, envelope, before_dot=None):
        raise Died
    monkeypatch.setattr(transport, "send", dies)
    with pytest.raises(Died):
        outbox.send_one(oid)
    monkeypatch.setattr(transport, "send", real)
    c = store.conn()
    outbox.QUEUE.reset_stuck(c)
    c.commit()
    c.close()
    assert row(oid)["state"] == "queued"
    outbox.drain()
    assert row(oid)["state"] == "sent" and len(world.smtp.messages) == 1


def test_a_big_message_lost_part_way_through_is_tried_again(app, world, monkeypatch):
    """A send() that raises left the last bytes unsent: no one can have the whole message."""
    import smtplib

    from mailmind.bootstrap.compose import outbox
    oid = queued(app)
    real = smtplib.SMTP.send

    def breaks_on_the_body(self, data):
        if isinstance(data, bytes):
            self.close()
            raise OSError("connection reset")
        return real(self, data)
    monkeypatch.setattr(smtplib.SMTP, "send", breaks_on_the_body)
    outbox.drain()
    monkeypatch.setattr(smtplib.SMTP, "send", real)
    r = row(oid)
    assert (r["state"], r["wire_begun"]) == ("queued", 0)
    later(app)
    assert row(oid)["state"] == "sent" and len(world.smtp.messages) == 1
