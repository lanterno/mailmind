"""What the outbox publishes on the in-process bus as a send goes out, with the app in the test's own process.

The rest of the send path is in tests/api/test_http_send.py, against a server the suite starts. This stays here because
it listens on mailmind.platform.bus, which only the app's own process has, and over HTTP a send that went out and an
attempt that failed nudge the page alike ("outbox" on /api/events), so no answer tells Sent from Failed.
"""
from datetime import timedelta

from fixtures import world as w
from mailmind.compose.api import Sent


def new(**kw):
    return {"account": w.ADA, "mode": "new", "to": ["Lin Mori <lin@example.test>"], "subject": "Fabric order",
            "text": "Hi Lin,\n\nThe linen came in. Want half?\n\nAda", **kw}


def test_a_send_is_published_once_it_goes_out_and_not_while_it_fails(app, world):
    from mailmind.bootstrap.compose import outbox
    from mailmind.platform import bus
    heard = []
    stop = bus.subscribe(Sent, heard.append)
    try:
        world.smtp.fail("RCPT", "451 4.3.0 try again later")
        oid = app.post("/api/send", new(idempotency_key="k-heard")).json["outbox_id"]
        app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
        outbox.drain()
        assert heard == []
        app.clock.shift(timedelta(seconds=outbox._BACKOFF_CAP))
        outbox.drain()
        outbox.drain()
    finally:
        stop()
    assert heard == [Sent(oid, w.ADA)]
