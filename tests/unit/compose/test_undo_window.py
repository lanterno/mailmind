"""How long a send waits before it goes: the preference, its bounds, and that the page is told what to count."""
import json
import time
from datetime import timedelta

import pytest

from fixtures import world as w
from mailmind.bootstrap.compose import outbox
from mailmind.kernel import send_window


def new(key="k-1", **kw):
    return {"account": w.ADA, "to": ["lin@example.test"], "subject": "Thread colours", "text": "Which blue?",
            "idempotency_key": key, **kw}


def due_of(oid):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return c.execute("SELECT next_attempt_at FROM outbox WHERE id=?", (oid,)).fetchone()[0]
    finally:
        c.close()


def choose(world, seconds):
    path = world.dir / "mailmind.config.json"
    raw = json.loads(path.read_text())
    raw["preferences"] = {"undo_send_seconds": seconds}
    path.write_text(json.dumps(raw))


def test_with_nothing_set_a_send_waits_the_default_and_says_so(app):
    r = app.post("/api/send", new()).json
    assert r["undo_seconds"] == send_window.DEFAULT == outbox.SEND_DELAY
    assert due_of(r["outbox_id"]) == time.time() + send_window.DEFAULT


def test_a_chosen_window_is_what_a_send_waits_and_what_the_page_counts(app, world):
    choose(world, 30)
    r = app.post("/api/send", new()).json
    assert r["undo_seconds"] == 30
    assert due_of(r["outbox_id"]) == time.time() + 30
    app.clock.shift(timedelta(seconds=29))
    outbox.drain()
    assert app.post("/api/outbox/cancel", {"id": r["outbox_id"]}).status == 200      # still undoable just before its end


def test_a_chosen_window_shorter_than_the_default_goes_sooner(app, world):
    choose(world, send_window.SHORTEST)
    oid = app.post("/api/send", new()).json["outbox_id"]
    app.clock.shift(timedelta(seconds=send_window.SHORTEST + 1))
    outbox.drain()
    assert len(world.smtp.messages) == 1, "sent once its window has passed"
    assert app.post("/api/outbox/cancel", {"id": oid}).status != 200


@pytest.mark.parametrize("junk", [0, 1, 3600, "soon", None, [30], True])
def test_a_window_edited_by_hand_that_cant_be_one_is_the_default(app, world, junk):
    choose(world, junk)
    assert outbox.send_delay() == outbox.SEND_DELAY


def test_a_file_that_wont_parse_leaves_a_send_at_the_default(world):
    from mailmind.bootstrap import accounts
    (world.dir / "mailmind.config.json").write_text("{ not json")
    with pytest.raises(ValueError):
        accounts.config.preference("undo_send_seconds")
    assert outbox.send_delay() == outbox.SEND_DELAY


def test_an_rsvp_tells_the_page_the_same_window(app, world):
    choose(world, 20)
    r = app.post("/api/invite/rsvp", {**world.copy("ada-inbox-06-outlook-invite.eml"), "decision": "accept"}).json
    assert r["undo_seconds"] == 20
    assert due_of(r["outbox_id"]) == time.time() + 20
