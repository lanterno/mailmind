"""Undo when it can't be done: a refusal or a lost connection keeps the entry, a group reports what it reversed, and the stack's size."""
import problems
from fixtures import world as w
from stored_rows import q
from world_helpers import FOLDED, RECEIPT, boxes, server_boxes, stored


def test_hwf_a_failed_undo_stays_on_the_stack_for_a_retry(app, world):
    assert app.post("/api/archive", {**world.copy(RECEIPT)}).json["ok"]
    world.server.fail("UID SEARCH", user=w.ADA)              # the server can't find it this once
    r = app.post("/api/undo", {})
    assert r.status == 409 and server_boxes(world, RECEIPT) == [(w.ADA, "Archive")]
    assert app.post("/api/undo", {}).json["ok"]
    assert server_boxes(world, RECEIPT) == [(w.ADA, "INBOX")]


def test_hwf_undo_keeps_the_last_50_actions(app, world):
    receipt = world.copy(RECEIPT)
    for _ in range(51):
        assert app.post("/api/snooze", {**receipt, "preset": "tomorrow"}).json["ok"]
    answers = [app.post("/api/undo", {}).json for _ in range(51)]
    assert [a.get("ok") for a in answers] == [True] * 50 + [None]
    assert answers[-1] == problems.not_found(empty=True)


def archived_pair(app, world):
    for name in (RECEIPT, FOLDED):
        assert app.post("/api/archive", {**world.copy(name), "undo_group": "g1"}).json["ok"]


def test_a_partly_failed_group_undo_keeps_what_it_couldnt_reverse(app, world):
    archived_pair(app, world)
    world.server.fail("UID SEARCH", user=w.ADA)              # the first child reversed (FOLDED) isn't found
    r = app.post("/api/undo", {}).json
    assert (r["ok"], r["reversed"], r["failed"]) == (True, 1, 1)
    assert app.post("/api/undo", {}).json["ok"]
    assert server_boxes(world, FOLDED) == [(w.ADA, "INBOX")]


def test_a_refusal_partway_through_a_group_undo_still_reports_what_it_reversed(app, world):
    archived_pair(app, world)
    [uid] = world.server.find(w.ADA, "Archive", w.message_id(RECEIPT))
    for cmd in ("UID MOVE", "UID COPY"):                     # RECEIPT, reversed second, can't move back
        world.server.fail(cmd, when=lambda args, uid=uid: str(args[0]) == str(uid), user=w.ADA)
    r = app.post("/api/undo", {}).json
    assert server_boxes(world, FOLDED) == [(w.ADA, "INBOX")]
    assert (r["ok"], r.get("reversed"), r.get("failed")) == (True, 1, 1)


def test_a_group_undo_that_reverses_nothing_keeps_the_whole_group(app, world):
    archived_pair(app, world)
    world.server.drop("LOGIN", user=w.ADA, times=2)          # neither member can reach the server
    assert app.post("/api/undo", {}).status == 409
    r = app.post("/api/undo", {}).json
    assert (r["ok"], r["reversed"], r["failed"]) == (True, 2, 0)
    assert server_boxes(world, RECEIPT) == server_boxes(world, FOLDED) == [(w.ADA, "INBOX")]


def test_hwf_a_refused_gmail_unarchive_is_not_reported_as_undone(app, world):
    name = "cy-inbox-03-unrelated.eml"
    assert app.post("/api/archive", world.copy(name)).json["ok"]
    world.server.fail("UID STORE", user=w.CY)
    assert app.post("/api/undo", {}).status == 409
    assert app.post("/api/undo", {}).json["ok"]                  # kept for a retry, which works
    assert (w.CY, "INBOX") in boxes(world, name)[0]


def test_hwf_a_refused_move_back_is_not_reported_as_undone(app, world):
    assert app.post("/api/archive", world.copy(RECEIPT)).json["ok"]
    world.server.fail("UID MOVE", user=w.ADA)
    world.server.fail("UID COPY", user=w.ADA)
    assert app.post("/api/undo", {}).status == 409
    assert boxes(world, RECEIPT)[0] == [(w.ADA, "Archive")]
    assert app.post("/api/undo", {}).json["ok"]
    assert boxes(world, RECEIPT)[0] == [(w.ADA, "INBOX")]


def test_hwf_a_malformed_message_id_is_quoted_so_undo_finds_it(app, world):
    mid = "<b'AB\"x\\y@example.test>"                            # a quote and a backslash, as some senders mint
    raw = w.raw(RECEIPT).replace(w.message_id(RECEIPT).encode(), mid.encode(), 1)
    uid = world.deliver(w.ADA, "INBOX", raw)
    [pk] = [r["pk"] for r in q("SELECT pk FROM messages WHERE account=? AND folder='INBOX' AND uid=?", w.ADA, uid)]
    assert app.post("/api/trash", w.copy_of(pk)).json["ok"]
    assert app.post("/api/undo", {}).json["ok"]
    assert world.server.find(w.ADA, "INBOX", mid) and mid in stored().values()
