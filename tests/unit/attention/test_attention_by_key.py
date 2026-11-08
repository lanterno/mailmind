"""Attention's routes name what they act on by key: a snooze its copies by key and folder, and mute, reply later and a
follow-up's dismissal the message of a thread by its key, which still names it after a renumber gives its row a new pk."""
from fixtures import world as w
from mailmind.bootstrap import accounts
from mailmind.bootstrap.mailbox import ingest, store

LUNCH = "ada-inbox-02-lunch-confirm.eml"
RECEIPT = "ada-inbox-05-receipt.eml"
CI = "ada-inbox-04-ci.eml"


def q(sql, *params):
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, params)]
    finally:
        c.close()


def listed(app, folder="INBOX"):
    return {i["subject"] for i in app.get(f"/api/messages?account={w.ADA}&folder={folder}&limit=100").json["items"]}


def renumber_inbox(world):
    world.server.renumber(w.ADA, "INBOX")
    ingest.sync_new(accounts.get(w.ADA))


def test_copies_are_snoozed_and_woken_by_their_key_and_folder(app, world):
    subjects = {world.row(RECEIPT)["subject"], world.row(CI)["subject"]}
    r = app.post("/api/snooze", {"copies": [world.copy(RECEIPT), world.copy(CI)], "preset": "tomorrow"}).json
    assert r["count"] == 2 and not subjects & listed(app) and subjects <= listed(app, "__snoozed__")
    assert app.post("/api/unsnooze", {"copies": [world.copy(RECEIPT), world.copy(CI)]}).json["count"] == 2
    assert subjects <= listed(app)


def test_a_snoozed_copy_is_woken_by_its_key_after_a_renumber(app, world):
    subject = world.row(RECEIPT)["subject"]
    assert app.post("/api/snooze", {"copies": [world.copy(RECEIPT)], "preset": "tomorrow"}).json["count"] == 1
    renumber_inbox(world)
    assert app.post("/api/unsnooze", {"copies": [world.copy(RECEIPT)]}).json["count"] == 1
    assert subject in listed(app)


def test_a_copy_that_has_gone_is_left_out_of_a_snooze(app, world):
    gone = {"key": world.key(CI), "folder": "Archive"}
    r = app.post("/api/snooze", {"copies": [world.copy(RECEIPT), gone], "preset": "tomorrow"}).json
    assert r["count"] == 1 and world.row(CI)["subject"] in listed(app)


def test_a_snooze_names_one_copy_by_its_key_and_folder(app, world):
    assert app.post("/api/snooze", {**world.copy(RECEIPT), "preset": "tomorrow"}).json["count"] == 1
    assert world.row(RECEIPT)["subject"] not in listed(app)


def test_a_thread_is_muted_by_the_key_of_a_message_in_it(app, world):
    thread = world.row(LUNCH)["thread_id"]
    renumber_inbox(world)
    r = app.post("/api/mute", {"key": world.key(LUNCH), "folder": "INBOX"}).json
    assert r["thread_id"] == thread and q("SELECT thread_id FROM muted") == [{"thread_id": thread}]
    assert app.post("/api/unmute", {"key": world.key(LUNCH)}).json["thread_id"] == thread
    assert q("SELECT thread_id FROM muted") == []


def test_reply_later_pins_by_key_and_a_dismissal_goes_by_key(app, world):
    thread = world.row(LUNCH)["thread_id"]
    r = app.post("/api/replylater", {"key": world.key(LUNCH), "folder": "INBOX"}).json
    assert (r["thread_id"], r["on"]) == (thread, True)
    assert app.post("/api/followups/dismiss", {"key": world.key(LUNCH)}).json["thread_id"] == thread


def test_a_thread_named_by_a_key_with_no_copy_left_is_no_thread(app, world):
    for path, detail in (("/api/mute", "bad mute target"), ("/api/unmute", "bad mute target"), ("/api/replylater", "bad target"),
                         ("/api/followups/dismiss", "bad target")):
        r = app.post(path, {"key": 987654})
        assert (r.status, r.json["detail"]) == (400, detail), path
