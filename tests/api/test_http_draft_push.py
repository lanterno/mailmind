"""A draft's push to its server, over HTTP. The push job waits its turn for a moment while another job holds the
account, and the copy another app left beside it under the same Message-ID goes with the one the draft was opened from,
so Drafts holds one."""
import threading
from datetime import timedelta

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
