"""A move when the server refuses it or can't be reached: what stays, what queues, and what the worker does with it."""
from datetime import timedelta

from fixtures import world as w
from mailmind.accounts.domain import login_hold
from stored_rows import q, rows
from world_helpers import LUNCH_CONFIRM, RECEIPT, acct, boxes, gone, server_boxes


def test_hwf_a_trash_queued_offline_knows_its_trash_folder_and_undoes_after_it_ships(app, world):
    from mailmind.bootstrap.mailbox import commands
    world.server.drop("SELECT", user=w.ADA)
    r = app.post("/api/trash", {**world.copy(RECEIPT)}).json
    assert (r["status"], r["undoable"]) == ("queued", True)
    assert [x["dest"] for x in rows("SELECT dest FROM mail_commands")] == ["Trash"]   # known before any IMAP
    commands.drain()
    assert server_boxes(world, RECEIPT) == [(w.ADA, "Trash")]
    assert app.post("/api/undo", {}).json["ok"]
    assert server_boxes(world, RECEIPT) == [(w.ADA, "INBOX")]
    assert [(d["folder"]) for d in world.where(RECEIPT)["db"]] == ["INBOX"]


def test_marking_read_while_the_server_is_away_is_queued(app, world):
    world.server.drop("SELECT", user=w.ADA)
    r = app.post("/api/seen", {**world.copy(LUNCH_CONFIRM), "seen": True})
    assert (r.status, r.json.get("status"), world.row(LUNCH_CONFIRM)["seen"]) == (200, "queued", 1)


def queue(app, world, op="archive", name=RECEIPT, account=w.ADA, mutation_id="m-1"):
    """Ask for `op` on `name` while the server drops the connection: the move is queued."""
    world.server.drop("SELECT", user=account)
    r = app.post(f"/api/{op}", {**world.copy(name), "mutation_id": mutation_id}).json
    assert r["status"] == "queued", r
    return r


def queued():
    return q("SELECT id, kind AS op, state, attempts, applied, dest FROM mail_commands ORDER BY id")


def test_hwf_a_refused_select_keeps_the_message(app, world):
    for path in ("/api/trash", "/api/archive", "/api/junk"):
        world.server.fail("SELECT", user=w.ADA)
        since = world.mark()
        r = app.post(path, world.copy(RECEIPT))
        assert (r.status, r.json["type"]) == (409, "/problems/conflict"), path
        assert world.writes(since) == [], path
        assert boxes(world, RECEIPT) == ([(w.ADA, "INBOX")], [(w.ADA, "INBOX")]), path


def test_hwf_a_refused_login_queues_the_move_instead_of_dropping_it(app, world):
    from mailmind.bootstrap.mailbox import commands
    world.server.fail("LOGIN", "NO [AUTHENTICATIONFAILED] invalid credentials", user=w.ADA)
    r = app.post("/api/archive", {**world.copy(RECEIPT), "mutation_id": "m-1"}).json
    assert r["status"] == "queued"
    assert boxes(world, RECEIPT) == ([(w.ADA, "INBOX")], [])
    app.clock.shift(timedelta(seconds=login_hold.FIRST + 1))     # it isn't sent again before then
    commands.drain()                                             # the login works again
    assert boxes(world, RECEIPT)[0] == [(w.ADA, "Archive")]


def test_hwf_the_queue_finds_its_message_by_message_id_not_its_old_uid(app, world):
    from mailmind.bootstrap.mailbox import commands
    queue(app, world)
    gone(world, "ada-inbox-01-lunch.eml")
    world.server.renumber(w.ADA, "INBOX")                       # the receipt's old UID now names the invite
    commands.drain()
    assert boxes(world, RECEIPT)[0] == [(w.ADA, "Archive")]
    assert boxes(world, "ada-inbox-06-outlook-invite.eml")[0] == [(w.ADA, "INBOX")]


def test_hwf_a_queued_move_undone_while_the_worker_waits_for_the_lock_never_ships(app, world, monkeypatch):
    from mailmind.bootstrap.mailbox import commands
    from mailmind.platform.workers import gates
    queue(app, world)
    since, undone = world.mark(), []

    class TheUndoGetsTheGateFirst:
        """The gate as the queue's worker sees it: when the worker, having claimed the row, asks
        for the gate, the user's undo takes it first."""
        def __getattr__(self, name):
            return getattr(gates, name)

        def run(self, account, fn, **kw):
            undone.append(app.post("/api/undo", {}).json["ok"])
            return gates.run(account, fn, **kw)
    monkeypatch.setattr(commands, "gates", TheUndoGetsTheGateFirst())
    commands.drain()                                            # claimed, then cancelled before it got the lock
    assert undone == [True]
    assert world.writes(since) == []
    assert queued() == []
    assert boxes(world, RECEIPT) == ([(w.ADA, "INBOX")], [(w.ADA, "INBOX")])


def test_hwf_an_offline_trash_undone_while_offline_comes_back(app, world):
    from mailmind.bootstrap.mailbox import ingest
    name = "ada-inbox-09-rfc2047-folded.eml"
    queue(app, world, op="trash", name=name)
    world.server.drop("LOGIN", user=w.ADA, times=None)           # still unreachable when Undo is pressed
    app.post("/api/undo", {})
    world.server.clear_faults()
    app.post("/api/undo", {})                                    # back online: Undo again, then a sync
    ingest.sync_new(acct())
    assert boxes(world, name) == ([(w.ADA, "INBOX")], [(w.ADA, "INBOX")])


def test_hwf_a_queued_move_for_a_removed_account_is_given_up(app, world):
    from mailmind.bootstrap.accounts import config
    from mailmind.bootstrap.mailbox import commands
    queue(app, world)
    assert config.remove_account(w.ADA)
    commands.drain()
    assert queued()[0]["state"] == "error"
