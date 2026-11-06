"""Filing a whole conversation (/api/thread/archive and /api/thread/trash): which messages it takes,
one Undo for all of them, and what a failure leaves. Through each door, on the fake IMAP server."""
from fixtures import world as w

LUNCH = "ada-inbox-01-lunch.eml"
CONFIRM = "ada-inbox-02-lunch-confirm.eml"
REPLY = "ada-sent-01-lunch-reply.eml"
RECEIPT = "ada-inbox-05-receipt.eml"
CI = "ada-inbox-04-ci.eml"
CLIENT = "bea-inbox-01-client.eml"


def boxes(world, name):
    return [box for _, box, _, _ in world.where(name)["server"]]


def stack():
    from mailmind.bootstrap.mailbox import store
    from mailmind.platform import undo
    c = store.conn()
    try:
        return undo.entries(c)
    finally:
        c.close()


def set_thread(world, name, thread_id):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    c.execute("UPDATE messages SET thread_id=? WHERE pk=?", (thread_id, world.pk(name)))
    c.commit()
    c.close()


def refuse_moves(world, *names):
    """The server refuses to move these, or with no names, anything."""
    uids = {str(world.uid(n)) for n in names}
    when = (lambda a: str(a[0]) in uids) if names else None
    world.server.fail("UID MOVE", user=w.ADA, times=None, when=when)
    world.server.fail("UID COPY", user=w.ADA, times=None, when=when)


def test_files_every_message_of_the_thread_in_the_anchors_folder(app, world):
    pks = sorted([world.pk(LUNCH), world.pk(CONFIRM)])
    set_thread(world, CLIENT, world.row(LUNCH)["thread_id"])   # the same id on another account: not this one
    r = app.post("/api/thread/archive", {"pk": world.pk(CONFIRM)}).json
    assert (r["ok"], r["count"], r["failed"], sorted(r["pks"])) == (True, 2, 0, pks)
    assert boxes(world, LUNCH) == ["Archive"] and boxes(world, CONFIRM) == ["Archive"]
    assert boxes(world, REPLY) == ["Sent"]                     # your reply lives in Sent: not this row's
    assert boxes(world, RECEIPT) == ["INBOX"]                  # a different conversation
    assert boxes(world, CLIENT) == ["INBOX"]


def test_any_message_can_anchor_the_conversation(app, world):
    pks = sorted([world.pk(LUNCH), world.pk(CONFIRM)])
    r = app.post("/api/thread/trash", {"pk": world.pk(LUNCH)}).json
    assert sorted(r["pks"]) == pks


def test_a_message_without_a_thread_is_a_conversation_of_one(app, world):
    set_thread(world, RECEIPT, None)
    set_thread(world, CI, "")
    pk = world.pk(RECEIPT)
    r = app.post("/api/thread/archive", {"pk": pk}).json
    assert (r["pks"], r["count"]) == ([pk], 1)
    assert boxes(world, RECEIPT) == ["Archive"] and boxes(world, CI) == ["INBOX"]


def test_the_trash_route_uses_trash(app, world):
    r = app.post("/api/thread/trash", {"pk": world.pk(CONFIRM)}).json
    assert r["ok"] and boxes(world, LUNCH) == ["Trash"] and boxes(world, CONFIRM) == ["Trash"]
    assert stack()[-1]["label"] == "Trashed"


def test_one_undo_entry_for_the_whole_conversation(app, world):
    r = app.post("/api/thread/archive", {"pk": world.pk(CONFIRM)}).json
    [entry] = stack()
    assert r["undoable"] and entry["kind"] == "bulk" and len(entry["children"]) == 2
    assert app.post("/api/undo", {}).json["ok"]
    assert boxes(world, LUNCH) == ["INBOX"] and boxes(world, CONFIRM) == ["INBOX"]


def test_a_bulk_selection_shares_the_callers_undo_group(app, world):
    app.post("/api/thread/archive", {"pk": world.pk(CONFIRM), "undo_group": "g-1"})
    app.post("/api/thread/archive", {"pk": world.pk(RECEIPT), "undo_group": "g-1"})
    [entry] = stack()
    assert len(entry["children"]) == 3


def test_a_partial_failure_reports_what_was_filed(app, world):
    refuse_moves(world, LUNCH)
    confirm = world.pk(CONFIRM)
    r = app.post("/api/thread/archive", {"pk": confirm}).json
    assert (r["ok"], r["count"], r["failed"], r["pks"]) == (True, 1, 1, [confirm])
    assert boxes(world, LUNCH) == ["INBOX"] and boxes(world, CONFIRM) == ["Archive"]
    assert world.row(LUNCH)["folder"] == "INBOX"


def test_a_total_failure_is_not_ok_and_leaves_nothing_to_undo(app, world):
    refuse_moves(world)
    r = app.post("/api/thread/archive", {"pk": world.pk(CONFIRM)}).json
    assert (r["ok"], r["pks"], r["failed"]) == (False, [], 2)
    assert r["msg"]
    assert boxes(world, LUNCH) == ["INBOX"] and world.row(LUNCH)["folder"] == "INBOX"
    assert stack() == []


def test_an_unreachable_server_queues_each_message(app, world):
    from mailmind.bootstrap.mailbox import store
    world.server.drop("SELECT", user=w.ADA, times=None)
    r = app.post("/api/thread/trash", {"pk": world.pk(CONFIRM)}).json
    assert (r["ok"], r["count"], r["queued"]) == (True, 2, 2)
    c = store.conn()
    queued = c.execute("SELECT COUNT(*) FROM mail_commands WHERE kind='trash' AND applied=0").fetchone()[0]
    c.close()
    assert queued == 2
    assert world.where(LUNCH)["db"] == [] and world.where(CONFIRM)["db"] == []   # out of the lists


def test_a_missing_or_bad_anchor(app):
    assert app.post("/api/thread/archive", {"pk": 999}).json["ok"] is False
    assert app.post("/api/thread/archive", {"pk": "x"}).json["msg"] == "bad pk"
    assert app.post("/api/thread/archive", {}).json["msg"] == "bad pk"
