"""A draft's push to its server, over HTTP. The push job waits its turn for a moment while another job holds the
account, a push tells the open lists of Drafts, its first too, and the copy another app left beside it under the same
Message-ID goes with the one the draft was opened from, so Drafts holds one."""
import json
import threading
from datetime import timedelta

from event_stream import Stream
from fixtures import world as w

NOTES = "ada-drafts-02-thunderbird-v2.eml"     # Thunderbird's second save of it; its first is beside it, same Message-ID


def notes_in_drafts(world):
    """The copies of "Notes for the review" in Ada's Drafts on the server, as the text each holds."""
    with world.server.lock:
        return [m.raw for m in world.server.mailbox(w.ADA, "Drafts").messages.values()
                if "\\Deleted" not in m.flags and b"Notes for the review" in m.raw]


def swatches_in_drafts(world):
    """The copies of the draft these tests save, in Ada's Drafts on the server."""
    with world.server.lock:
        return [m.raw for m in world.server.mailbox(w.ADA, "Drafts").messages.values()
                if "\\Deleted" not in m.flags and b"Swatch order" in m.raw]


def listed(app, subject):
    return [i for i in app.get(f"/api/messages?account={w.ADA}&folder=Drafts").json["items"] if i["subject"] == subject]


def test_a_draft_another_app_saved_twice_has_one_copy_once_saved_here(app, world):
    assert len(notes_in_drafts(world)) == 2 and len(listed(app, "Notes for the review")) == 2
    opened = app.post("/api/draft/resume", world.copy(NOTES)).json
    r = app.post("/api/draft", {"account": w.ADA, "mode": "new", "to": ["noor@studio.example.test"],
                                "subject": "Notes for the review", "text": "Settled on the wider grid.",
                                "draft_id": opened["draft_id"], "version": opened["version"]})
    assert r.status == 200 and r.json["pushed"] is True, r.json
    [left] = notes_in_drafts(world)
    assert b"Settled on the wider grid." in left
    assert len(listed(app, "Notes for the review")) == 1


def test_the_push_job_waits_its_turn_while_another_job_holds_the_account(app, world):
    r = app.post("/api/draft", {"account": w.ADA, "mode": "new", "to": ["lin@example.test"], "subject": "Swatch order",
                                "text": "Two of each.", "autosave": True})
    assert r.status == 200 and r.json["pushed"] is False, r.json
    app.clock.shift(timedelta(minutes=2))
    world.server.hold("UID MOVE", user=w.ADA)
    filed = threading.Thread(target=lambda: app.post("/api/archive", world.copy("ada-inbox-05-receipt.eml")))
    filed.start()
    assert world.server.held()               # the archive holds Ada's account, waiting for its server
    # It lets go a moment after the job asks: a job that only asked whenever the account was free found it taken.
    threading.Timer(0.5, world.server.let_go).start()
    app.work("drafts")
    filed.join(30)
    [copy] = swatches_in_drafts(world)
    assert b"Two of each." in copy
    assert app.get("/api/outbox").json["drafts"] == []


def test_every_copy_failed_pushes_left_goes_once_one_lands(app, world):
    # Each try's APPEND lands, and the connection drops before the server says so: no try knows of its copy. The first
    # save's goes by the Message-ID the draft was made with; the second's only by what the tries kept.
    world.server.drop_after("APPEND", user=w.ADA, times=3)
    swatch = {"account": w.ADA, "mode": "new", "to": ["lin@example.test"], "subject": "Swatch order"}
    saved = None
    for text in ("Two of each.", "Three of each.", "Four of each."):
        named = {"draft_id": saved["draft_id"], "base_version": saved["version"]} if saved else {}
        r = app.post("/api/draft", {**swatch, "text": text, **named})
        assert r.status == 200 and r.json["pushed"] is False, r.json
        saved = r.json
    assert len(swatches_in_drafts(world)) == 3               # a copy each try left
    app.clock.shift(timedelta(minutes=2))
    app.work("drafts")                                       # the next try lands
    [copy] = swatches_in_drafts(world)
    assert b"Four of each." in copy
    assert app.get("/api/outbox").json["drafts"] == []


def heard_next(tab):
    """The next event the tab hears, as (event, data)."""
    head, data = tab.event().split("\n")[1:3]
    return head.removeprefix("event: "), json.loads(data.removeprefix("data: "))


def test_a_first_push_tells_the_open_lists_of_drafts(app, world):
    tab = Stream(app)
    tab.frame()
    r = app.post("/api/draft", {"account": w.ADA, "mode": "new", "to": ["lin@example.test"], "subject": "Swatch order",
                                "text": "Two of each."})
    assert r.status == 200 and r.json["pushed"] is True, r.json
    heard = []
    while (event := heard_next(tab))[0] != "outbox":     # the tray's word comes last
        heard.append(event)
    assert ("reconcile", {"account": w.ADA, "folder": "Drafts", "pruned": 1}) in heard
    tab.close()
