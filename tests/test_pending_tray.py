"""The pending-changes tray: moves and flag changes that haven't reached a server, /api/pending,
with Retry for what the queue gave up on and Discard for any of them."""
import pytest

from fixtures import world as w

RECEIPT = "ada-inbox-05-receipt.eml"
LUNCH = "ada-inbox-02-lunch-confirm.eml"


def inbox_pks(app):
    return {i["pk"] for i in app.get(f"/api/messages?account={w.ADA}&folder=INBOX&limit=100").json["items"]}


def tray(app):
    return app.get("/api/pending").json


def queue_archive(app, world, name=RECEIPT, group=None):
    world.server.drop("SELECT", user=w.ADA)
    body = {"pk": world.pk(name), "mutation_id": f"m-{name}"}
    if group:
        body["undo_group"] = group
    assert app.post("/api/archive", body).json["status"] == "queued"
    return tray(app)["items"][0]["id"]


def queue_star(app, world, name=RECEIPT):
    world.server.drop("SELECT", user=w.ADA)
    assert app.post("/api/flag", {"pk": world.pk(name), "flag": "flagged", "on": True}).json["status"] == "queued"
    return tray(app)["items"][0]["id"]


def refused(world, *commands):
    for command in commands:
        world.server.fail(command, user=w.ADA)


def row_flagged(world, name=RECEIPT):
    return world.row(name)["flagged"]


def state_of(mid):
    import store
    c = store.conn()
    try:
        r = store.read_mut_get(c, mid)
        return r and r["state"]
    finally:
        c.close()


def test_a_queued_move_shows_with_what_the_tray_needs_and_leaves_once_it_lands(app, world):
    import readmutq
    assert tray(app) == {"items": [], "waiting": 0, "stuck": 0}
    queue_archive(app, world)
    got = tray(app)
    [item] = got["items"]
    assert (got["waiting"], got["stuck"]) == (1, 0)
    assert (item["op"], item["folder"], item["account"], item["state"]) == ("archive", "INBOX", w.ADA, "queued")
    assert item["subject"] == world.row(RECEIPT)["subject"]
    readmutq.drain()
    assert tray(app) == {"items": [], "waiting": 0, "stuck": 0}


def test_a_queued_flag_change_names_its_message_and_what_it_asks(app, world):
    subject = world.row(RECEIPT)["subject"]
    queue_star(app, world)
    [item] = tray(app)["items"]
    assert (item["op"], item["flags"], item["subject"]) == ("flag", {"flagged": True}, subject)


def test_a_change_the_server_refused_stays_in_the_tray_as_stuck(app, world):
    import readmutq
    mid = queue_archive(app, world)
    refused(world, "UID MOVE", "UID COPY")
    readmutq.drain()
    got = tray(app)
    assert (got["waiting"], got["stuck"]) == (0, 1)
    assert (got["items"][0]["id"], got["items"][0]["state"], got["items"][0]["error"]) == (mid, "error", "refused")
    assert world.pk(RECEIPT) in inbox_pks(app)


def test_retry_takes_the_message_out_of_the_lists_again_and_the_move_lands(app, world):
    import readmutq
    pk = world.pk(RECEIPT)
    mid = queue_archive(app, world)
    refused(world, "UID MOVE", "UID COPY")
    readmutq.drain()
    assert pk in inbox_pks(app)
    assert app.post("/api/pending/retry", {"id": mid}).json == {"ok": True}
    assert pk not in inbox_pks(app)
    assert tray(app)["waiting"] == 1
    readmutq.drain()
    assert tray(app)["items"] == []
    assert [box for _, box, _, _ in world.where(RECEIPT)["server"]] != ["INBOX"]


def test_retry_of_a_flag_change_shows_it_and_a_new_refusal_puts_back_what_it_replaced(app, world):
    import readmutq
    import store
    mid = queue_star(app, world)
    assert row_flagged(world) == 1
    refused(world, "UID STORE")
    readmutq.drain()
    assert row_flagged(world) == 0
    assert app.post("/api/pending/retry", {"id": mid}).json == {"ok": True}
    assert row_flagged(world) == 1
    c = store.conn()
    r = store.read_mut_get(c, mid)
    c.close()
    assert '"was": {"flagged": false}' in r["payload"]
    refused(world, "UID STORE")
    readmutq.drain()
    assert row_flagged(world) == 0
    assert state_of(mid) == "error"


def test_retry_answers_404_for_what_isnt_stuck(app, world):
    mid = queue_archive(app, world)
    assert app.post("/api/pending/retry", {"id": mid}).status == 404     # still waiting
    assert app.post("/api/pending/retry", {"id": 99999}).status == 404


def test_discard_lists_a_queued_move_again_and_it_never_reaches_the_server(app, world):
    import readmutq
    pk = world.pk(RECEIPT)
    mid = queue_archive(app, world)
    uid = world.uid(RECEIPT)
    assert pk not in inbox_pks(app)
    assert app.post("/api/pending/discard", {"id": mid}).json == {"ok": True}
    assert pk in inbox_pks(app)
    assert tray(app)["items"] == []
    readmutq.drain()
    assert uid in world.server.mailbox(w.ADA, "INBOX").messages


def test_after_a_discard_undo_has_nothing_to_reverse(app, world):
    mid = queue_archive(app, world)
    assert app.post("/api/pending/discard", {"id": mid}).json["ok"]
    assert app.post("/api/undo", {}).json.get("empty") is True


def test_discarding_one_of_a_group_leaves_undo_the_rest(app, world):
    import store
    a = queue_archive(app, world, RECEIPT, group="g-1")
    queue_archive(app, world, LUNCH, group="g-1")
    assert app.post("/api/pending/discard", {"id": a}).json["ok"]
    c = store.conn()
    [held] = store.undo_entries(c)
    c.close()
    assert [ch["message_id"] for ch in held["children"]] == [world.row(LUNCH)["message_id"]]


def test_discard_of_a_flag_change_puts_the_flags_back(app, world):
    mid = queue_star(app, world)
    assert row_flagged(world) == 1
    assert app.post("/api/pending/discard", {"id": mid}).json["ok"]
    assert row_flagged(world) == 0


def test_discard_of_a_given_up_move_clears_it_from_the_tray(app, world):
    import readmutq
    mid = queue_archive(app, world)
    refused(world, "UID MOVE", "UID COPY")
    readmutq.drain()
    assert app.post("/api/pending/discard", {"id": mid}).json["ok"]
    assert tray(app) == {"items": [], "waiting": 0, "stuck": 0}


def test_discard_reads_back_a_message_whose_row_went_when_it_was_queued(app, world):
    import store
    mid = queue_archive(app, world)
    message_id = world.row(RECEIPT)["message_id"]
    c = store.conn()                                  # as a move queued before migration 23 left it
    c.execute("DELETE FROM messages WHERE pending_removal=?", (mid,))
    c.commit()
    c.close()
    assert app.post("/api/pending/discard", {"id": mid}).json["ok"]
    c = store.conn()
    back = c.execute("SELECT 1 FROM messages WHERE account=? AND folder='INBOX' AND message_id=?",
                     (w.ADA, message_id)).fetchone()
    c.close()
    assert back


def test_discard_answers_404_once_the_change_reached_the_server(app, world):
    import readmutq
    mid = queue_archive(app, world)
    readmutq.drain()
    assert app.post("/api/pending/discard", {"id": mid}).status == 404
    assert app.post("/api/pending/discard", {"id": 99999}).status == 404


def test_discard_waits_for_a_try_under_the_gate_and_gives_up_as_busy(app, world, monkeypatch):
    import gate
    import server
    monkeypatch.setattr(server, "PENDING_WAIT", 0.05)
    mid = queue_archive(app, world)
    with gate.held_elsewhere(w.ADA):
        r = app.post("/api/pending/discard", {"id": mid})
    assert (r.status, r.json) == (409, {"ok": False, "busy": True})
    assert tray(app)["waiting"] == 1


@pytest.mark.parametrize("path", ["/api/pending/retry", "/api/pending/discard"])
def test_the_tray_routes_tell_the_open_tabs(app, world, path, monkeypatch):
    import readmutq
    from mailmind.platform import sse
    told = []
    real = sse.publish
    monkeypatch.setattr(sse, "publish", lambda topic, data=None: (told.append(topic), real(topic, data))[1])
    mid = queue_archive(app, world)
    assert told == ["outbox"]
    refused(world, "UID MOVE", "UID COPY")
    readmutq.drain()
    told.clear()
    assert app.post(path, {"id": mid}).json["ok"]
    assert told == ["commands-changed"]
