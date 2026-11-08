"""The tasks a person confirmed: kept under the message's key, so a renumber leaves them be, listed with a link back to the copy
it has now, marked done and removed. The message leaving takes only the link."""
from fixtures import world as w
from mailmind.bootstrap import accounts
from mailmind.bootstrap.mailbox import ingest, store

LUNCH = "ada-inbox-02-lunch-confirm.eml"
RECEIPT = "ada-inbox-05-receipt.eml"


def q(sql, *params):
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, params)]
    finally:
        c.close()


def add(app, world, name=LUNCH, title="Send the slides", due="2026-11-09"):
    return app.post("/api/tasks", {**world.copy(name), "title": title, "due": due})


def items(app):
    return app.get("/api/tasks").json["items"]


def test_a_task_is_kept_with_its_date_and_a_link_to_the_message(app, world):
    r = add(app, world)
    assert r.status == 200
    row = world.row(LUNCH)
    assert r.json["task"]["title"] == "Send the slides" and r.json["task"]["due"] == "2026-11-09"
    assert r.json["task"]["done"] is False
    assert (r.json["task"]["key"], r.json["task"]["folder"], r.json["task"]["uid"]) == (world.key(LUNCH), "INBOX", row["uid"])
    assert r.json["task"]["thread_id"] == row["thread_id"] and r.json["task"]["subject"] == row["subject"]
    assert items(app) == [r.json["task"]]


def test_the_row_names_the_message_by_key_and_no_pk(app, world):
    add(app, world)
    row = q("SELECT * FROM tasks")[0]
    assert row["message_key"] == world.key(LUNCH) and row["account"] == world.row(LUNCH)["account"]
    assert "pk" not in row and "message_pk" not in row
    assert "pk" not in items(app)[0]


def test_a_renumber_leaves_the_task_and_its_link(app, world):
    add(app, world)
    pk = world.pk(LUNCH)
    world.server.renumber(w.ADA, "INBOX")
    ingest.sync_new(accounts.get(w.ADA))
    assert world.pk(LUNCH) != pk
    got = items(app)[0]
    assert (got["key"], got["folder"], got["uid"]) == (world.key(LUNCH), "INBOX", world.row(LUNCH)["uid"])


def test_a_message_filed_elsewhere_is_linked_where_it_is_now(app, world):
    add(app, world)
    assert app.post("/api/archive", world.copy(LUNCH)).status == 200
    app.get(f"/api/messages?account={w.ADA}&folder=Archive&limit=100")          # opening it brings the copy here
    got = items(app)[0]
    assert (got["key"], got["folder"]) == (world.key(LUNCH), "Archive")


def test_a_message_gone_for_good_leaves_the_task_without_a_link(app, world):
    add(app, world)
    c = store.conn()
    try:
        c.execute("DELETE FROM messages WHERE message_key=?", (world.key(LUNCH),))
        c.commit()
    finally:
        c.close()
    got = items(app)[0]
    assert got["title"] == "Send the slides"
    assert (got["key"], got["folder"], got["uid"], got["subject"]) == (None, None, None, None)


def test_confirming_the_same_one_again_is_one_row_with_the_latest_date(app, world):
    first = add(app, world).json["task"]
    again = add(app, world, due="2026-11-20").json["task"]
    assert first["id"] == again["id"] and again["due"] == "2026-11-20"
    assert len(q("SELECT * FROM tasks")) == 1


def test_a_task_needs_no_date(app, world):
    r = app.post("/api/tasks", {**world.copy(LUNCH), "title": "Call Lin"})
    assert r.status == 200 and r.json["task"]["due"] is None
    assert app.post("/api/tasks", {**world.copy(RECEIPT), "title": "Check it", "due": ""}).json["task"]["due"] is None


def test_open_tasks_come_first_the_soonest_due_ahead_of_those_with_none(app, world):
    add(app, world, title="None")
    first = add(app, world, title="Later", due="2026-11-20")
    add(app, world, title="Soon", due="2026-11-05")
    add(app, world, title="Undated", due="")
    app.post("/api/tasks/done", {"id": first.json["task"]["id"]})
    assert [t["title"] for t in items(app)] == ["Soon", "None", "Undated", "Later"]


def test_a_task_is_marked_done_and_back(app, world):
    tid = add(app, world).json["task"]["id"]
    assert app.post("/api/tasks/done", {"id": tid}).json == {"ok": True}
    assert items(app)[0]["done"] is True
    assert app.post("/api/tasks/done", {"id": tid, "done": False}).status == 200
    assert items(app)[0]["done"] is False


def test_a_task_is_removed(app, world):
    tid = add(app, world).json["task"]["id"]
    assert app.post("/api/tasks/remove", {"id": tid}).json == {"ok": True}
    assert items(app) == [] and q("SELECT * FROM tasks") == []


def test_a_task_thats_gone_says_so(app):
    for path in ("/api/tasks/done", "/api/tasks/remove"):
        r = app.post(path, {"id": 987654})
        assert (r.status, r.json["detail"]) == (404, "that task is gone"), path


def test_what_wont_do_is_refused_in_words(app, world):
    no_title = add(app, world, title="   ")
    bad_date = add(app, world, due="next Friday")
    far_date = add(app, world, due="2199-01-01")
    assert (no_title.status, no_title.json["detail"]) == (400, "a task needs a title")
    assert (bad_date.status, bad_date.json["detail"]) == (400, "that date isn’t one this can use")
    assert (far_date.status, far_date.json["detail"]) == (400, "that date isn’t one this can use")
    assert q("SELECT * FROM tasks") == []


def test_naming_no_message_or_a_gone_one_makes_nothing(app):
    assert app.post("/api/tasks", {"title": "x"}).status == 400
    assert app.post("/api/tasks", {"key": 987654, "title": "x"}).status == 404
    assert q("SELECT * FROM tasks") == []


def test_a_title_comes_to_one_line(app, world):
    assert add(app, world, title="Sign\r\nthe\tform").json["task"]["title"] == "Sign the form"


def test_an_account_turned_off_has_its_tasks_kept_and_not_listed(app, world):
    add(app, world)
    accounts.config.update_account(w.ADA, {"disabled": True})
    assert items(app) == []
    assert len(q("SELECT * FROM tasks")) == 1


def test_the_tabs_are_told(app, world):
    from mailmind.platform import sse
    listener = sse.HUB.listen()
    seen = []
    try:
        tid = add(app, world).json["task"]["id"]
        app.post("/api/tasks/done", {"id": tid})
        app.post("/api/tasks/remove", {"id": tid})
        while len(seen) < 3:
            got = listener.next(timeout=1)
            assert got, "the stream fell silent"
            seen += [e.topic for e in got]
    finally:
        listener.close()
    assert seen.count("tasks-changed") == 3
