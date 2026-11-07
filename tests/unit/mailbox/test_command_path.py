"""The one path a change to the mail takes, online or offline: accepted in one transaction, tried, settled in one
transaction. What the routes answered before it came is pinned elsewhere; this is what it adds. Through each door, on the
fake IMAP server."""
import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from fixtures import world as w
from gate import held_elsewhere

RECEIPT = "ada-inbox-05-receipt.eml"
LUNCH = "ada-inbox-01-lunch.eml"


class Crash(BaseException):
    """The process died here: not an Exception, so no handler on the way out takes it for a failure."""


def rows():
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return [dict(r) for r in c.execute("SELECT * FROM mail_commands ORDER BY id")]
    finally:
        c.close()


def hidden(pk):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        row = store.get_message(c, pk)
        return row is not None and row["pending_removal"] is not None
    finally:
        c.close()


def restart():
    from mailmind.bootstrap.mailbox import commands, store
    c = store.conn()
    commands.QUEUE.reset_stuck(c)
    c.commit()
    c.close()


def undo_stack():
    from mailmind.bootstrap import mailbox
    return mailbox.undo.entries()


def boxes(world, name):
    return [box for _, box, _, _ in world.where(name)["server"]]


def dies_before(row, first_try=False):
    raise Crash


def test_a_move_that_lands_leaves_its_command_done(app, world):
    r = app.post("/api/archive", {"pk": world.pk(RECEIPT)}).json
    assert (r["ok"], r["msg"], r["undoable"]) == (True, "archived", True)
    [row] = rows()
    assert (row["kind"], row["state"], row["applied"], row["dest"], row["begun"]) == ("archive", "done", 1, "Archive", 1)
    assert boxes(world, RECEIPT) == ["Archive"]


def changes_sent(world, since, user=w.ADA):
    """What a try asked the server to change, by command: the lines after `since`, without their tags."""
    lines = [c["line"].split(" ", 1)[1] for c in world.server.commands[since:] if c["user"] == user]
    return [line for line in lines if line.startswith(("UID MOVE", "UID COPY", "UID STORE", "UID EXPUNGE"))]


def test_a_server_that_offers_move_gets_a_move(app, world):
    since = len(world.server.commands)
    assert app.post("/api/archive", {"pk": world.pk(RECEIPT)}).json["ok"]
    assert [" ".join(line.split(" ")[:2]) for line in changes_sent(world, since)] == ["UID MOVE"]
    assert boxes(world, RECEIPT) == ["Archive"]


def test_a_server_that_doesnt_gets_a_copy_then_an_expunge_of_that_uid(app, world):
    world.server.without = {"MOVE"}
    [(_, _, uid, _)] = world.where(RECEIPT)["server"]
    since = len(world.server.commands)
    assert app.post("/api/archive", {"pk": world.pk(RECEIPT)}).json["ok"]
    assert [line.split(" ")[:3] for line in changes_sent(world, since)] == [
        ["UID", "COPY", str(uid)], ["UID", "STORE", str(uid)], ["UID", "EXPUNGE", str(uid)]]
    assert boxes(world, RECEIPT) == ["Archive"]


def test_a_gmail_archive_only_drops_the_label(app, world):
    name = "cy-inbox-03-unrelated.eml"
    since = len(world.server.commands)
    assert app.post("/api/archive", {"pk": world.pk(name)}).json["ok"]
    [change] = changes_sent(world, since, w.CY)
    assert change.startswith("UID STORE") and "-X-GM-LABELS" in change and "Inbox" in change


def test_while_the_server_is_asked_the_message_is_hidden_and_undo_already_knows_it(app, world, monkeypatch):
    from mailmind.bootstrap.mailbox import server_ops
    pk, seen = world.pk(RECEIPT), {}
    apply = server_ops.apply

    def spy(row, first_try=False):
        seen.update(hidden=hidden(pk), stack=undo_stack(), first_try=first_try)
        return apply(row, first_try)
    monkeypatch.setattr(server_ops, "apply", spy)
    assert app.post("/api/trash", {"pk": pk}).json["ok"]
    assert seen["hidden"] is True and seen["first_try"] is True
    assert [e["command"] for e in seen["stack"]] == [rows()[0]["id"]]


def test_a_first_try_sends_no_search_of_its_own(app, world):
    since = world.mark()
    assert app.post("/api/archive", {"pk": world.pk(RECEIPT)}).json["ok"]
    assert not [c for c in world.server.commands[since:] if "SEARCH" in c["line"]]


def test_a_crash_in_a_click_before_the_server_is_asked_is_taken_up_at_the_next_start(app, world, monkeypatch):
    from mailmind.bootstrap.mailbox import commands, server_ops
    pk = world.pk(RECEIPT)
    with monkeypatch.context() as m:
        m.setattr(server_ops, "apply", dies_before)
        with pytest.raises(Crash):
            from mailmind.bootstrap import mailbox
            mailbox.filing.do_write("/api/archive", {"pk": pk})
    assert hidden(pk) and rows()[0]["state"] == "running"
    restart()
    since = world.mark()
    commands.drain()
    assert world.writes(since) == [f'{w.ADA} [INBOX] UID MOVE {world.uid(RECEIPT)} "Archive"']
    assert boxes(world, RECEIPT) == ["Archive"]
    assert rows()[0]["state"] == "done"


def test_a_crash_in_a_click_after_the_server_answered_never_moves_it_twice(app, world, monkeypatch):
    from mailmind.bootstrap import mailbox
    from mailmind.bootstrap.mailbox import commands, server_ops
    apply, pk = server_ops.apply, world.pk(RECEIPT)

    def dies_after(row, first_try=False):
        apply(row, first_try)
        raise Crash
    with monkeypatch.context() as m:
        m.setattr(server_ops, "apply", dies_after)
        with pytest.raises(Crash):
            mailbox.filing.do_write("/api/archive", {"pk": pk})
    assert boxes(world, RECEIPT) == ["Archive"] and hidden(pk)
    restart()
    since = world.mark()
    commands.drain()
    assert world.writes(since) == []
    [row] = rows()
    assert (row["state"], row["applied"]) == ("done", 1)
    assert world.where(RECEIPT)["db"] == []                             # its row went with the settling


def test_undo_after_a_crash_in_a_click_cancels_what_never_reached_the_server(app, world, monkeypatch):
    from mailmind.bootstrap import mailbox
    from mailmind.bootstrap.mailbox import server_ops
    pk = world.pk(RECEIPT)
    with monkeypatch.context() as m:
        m.setattr(server_ops, "apply", dies_before)
        with pytest.raises(Crash):
            mailbox.filing.do_trash({"pk": pk})
    restart()
    since = world.mark()
    assert app.post("/api/undo", {}).json["ok"]
    assert world.server.commands[since:] == [] and not hidden(pk) and rows() == []
    assert boxes(world, RECEIPT) == ["INBOX"]


def test_a_refusal_in_a_click_leaves_nothing_behind(app, world):
    from mailmind.bootstrap import mailbox
    pk = world.pk(RECEIPT)
    world.server.fail("UID MOVE", user=w.ADA)
    world.server.fail("UID COPY", user=w.ADA)
    r = app.post("/api/archive", {"pk": pk}).json
    assert (r["ok"], r["msg"], r["undoable"]) == (False, "the server refused the archive", False)
    assert rows() == [] and undo_stack() == [] and not hidden(pk)
    assert mailbox.commands.list_open()["items"] == []
    assert boxes(world, RECEIPT) == ["INBOX"]


def test_a_refused_flag_change_in_a_click_puts_back_what_it_replaced(app, world):
    pk, before = world.pk(RECEIPT), world.row(RECEIPT)["flagged"]
    world.server.fail("UID STORE", user=w.ADA)
    r = app.post("/api/flag", {"pk": pk, "flag": "flagged", "on": not before}).json
    assert (r["ok"], r["msg"]) == (False, "the server refused the flag change")
    assert world.row(RECEIPT)["flagged"] == before and rows() == []


def test_a_flag_change_shows_before_the_server_is_asked(app, world, monkeypatch):
    from mailmind.bootstrap.mailbox import server_ops
    pk, seen, apply = world.pk(RECEIPT), [], server_ops.apply

    def spy(row, first_try=False):
        seen.append(world.row(RECEIPT)["flagged"])
        return apply(row, first_try)
    monkeypatch.setattr(server_ops, "apply", spy)
    assert app.post("/api/flag", {"pk": pk, "flag": "flagged", "on": True}).json["msg"] == "ok"
    assert seen == [1] and world.row(RECEIPT)["flagged"] == 1
    assert rows()[0]["state"] == "done"


def test_a_busy_gate_hands_a_click_to_the_worker(app, world, monkeypatch):
    from mailmind.bootstrap.mailbox import commands
    monkeypatch.setattr(commands, "FILE_WAIT", 0.05)
    pk, since = world.pk(RECEIPT), world.mark()
    with held_elsewhere(w.ADA):
        r = app.post("/api/archive", {"pk": pk}).json
    assert (r["ok"], r["status"], r["undoable"]) == (True, "waiting", True)
    assert world.writes(since) == [] and hidden(pk)
    [row] = rows()
    assert (row["state"], row["attempts"], row["begun"]) == ("queued", 0, 0)
    commands.drain()
    assert world.writes(since) == [f'{w.ADA} [INBOX] UID MOVE {world.uid(RECEIPT)} "Archive"']
    assert rows()[0]["state"] == "done"


def test_a_message_on_its_way_out_cant_be_filed_again(app, world):
    pk = world.pk(RECEIPT)
    world.server.drop("SELECT", user=w.ADA)
    assert app.post("/api/archive", {"pk": pk}).json["status"] == "queued"
    assert app.post("/api/trash", {"pk": pk}).json == {"ok": False, "msg": "not found", "undoable": False}
    assert [r["kind"] for r in rows()] == ["archive"]


def test_a_try_that_raises_something_unforeseen_doesnt_leave_its_message_hidden_for_good(app, world, monkeypatch):
    from mailmind.bootstrap.mailbox import commands, server_ops
    pk = world.pk(RECEIPT)
    world.server.drop("SELECT", user=w.ADA)
    assert app.post("/api/archive", {"pk": pk}).json["status"] == "queued"

    def broken(row, first_try=False):
        raise ValueError("a bug")
    monkeypatch.setattr(server_ops, "apply", broken)
    commands.drain()
    [row] = rows()
    assert (row["state"], row["attempts"], row["last_error"]) == ("queued", 1, "ValueError")


def test_a_later_try_never_moves_what_another_message_now_holds_its_uid(app, world):
    """The message left its folder and the folder was renumbered: its UID names another message now."""
    from mailmind.bootstrap.mailbox import commands
    pk, uid = world.pk(RECEIPT), world.uid(RECEIPT)
    world.server.drop("SELECT", user=w.ADA)
    assert app.post("/api/archive", {"pk": pk}).json["status"] == "queued"
    box = world.server.accounts[w.ADA].box("INBOX")
    with world.server.lock:
        taken = box.messages.pop(uid)
    world.server.deliver(w.ADA, "Archive", taken.raw)
    world.server.renumber(w.ADA, "INBOX")
    others = dict(world.server.accounts[w.ADA].box("INBOX").messages)
    since = world.mark()
    commands.drain()
    assert world.writes(since) == []
    assert world.server.accounts[w.ADA].box("INBOX").messages.keys() == others.keys()
    assert rows()[0]["state"] == "done"


def test_a_uid_from_another_epoch_goes_nowhere_without_a_message_id(app, world):
    from mailmind.bootstrap.mailbox import commands, store
    pk = world.pk(RECEIPT)
    c = store.conn()
    c.execute("UPDATE messages SET message_id='' WHERE pk=?", (pk,))
    c.commit()
    c.close()
    world.server.drop("SELECT", user=w.ADA)
    assert app.post("/api/archive", {"pk": pk}).json["status"] == "queued"
    world.server.renumber(w.ADA, "INBOX")
    since = world.mark()
    commands.drain()
    assert world.writes(since) == []


def test_a_first_try_in_a_new_epoch_finds_the_message_by_its_id(app, world):
    from mailmind.bootstrap.mailbox import commands, store
    pk = world.pk(RECEIPT)
    c = store.conn()
    c.execute("UPDATE sync_state SET uidvalidity=uidvalidity+1 WHERE account=? AND folder='INBOX'", (w.ADA,))
    c.commit()
    c.close()
    assert app.post("/api/archive", {"pk": pk}).json["ok"]
    assert boxes(world, RECEIPT) == ["Archive"]
    assert commands.list_open()["items"] == []


def test_undo_takes_off_the_read_mark_a_server_set_on_the_way_to_trash(app, world, monkeypatch):
    from mailmind.bootstrap import mailbox
    from mailmind.bootstrap.mailbox import server_ops
    real = server_ops._accounts.profile
    monkeypatch.setattr(mailbox._directory, "profile", lambda acct: dataclasses.replace(real(acct), marks_trash_read=True))
    pk = world.pk(RECEIPT)
    assert not world.row(RECEIPT)["seen"]
    assert app.post("/api/trash", {"pk": pk}).json["ok"]
    assert "unseen" in (rows()[0]["outcome"] or "")
    assert app.post("/api/undo", {}).json["ok"]
    [(_, box, _, flags)] = world.where(RECEIPT)["server"]
    assert box == "INBOX" and "\\Seen" not in flags


def test_undo_of_a_plain_move_to_trash_takes_off_the_read_mark_too(app, world, monkeypatch):
    from mailmind.bootstrap import mailbox
    from mailmind.bootstrap.mailbox import server_ops
    real = server_ops._accounts.profile
    monkeypatch.setattr(mailbox._directory, "profile", lambda acct: dataclasses.replace(real(acct), marks_trash_read=True))
    pk = world.pk(RECEIPT)
    assert not world.row(RECEIPT)["seen"]
    assert app.post("/api/move", {"pk": pk, "dest": "Trash"}).json["ok"]
    assert "unseen" in (rows()[0]["outcome"] or "")
    assert app.post("/api/undo", {}).json["ok"]
    [(_, box, _, flags)] = world.where(RECEIPT)["server"]
    assert box == "INBOX" and "\\Seen" not in flags


def test_undo_of_an_older_entry_goes_by_the_move_and_not_a_later_flag_change(app, world):
    """An entry written before entries named their command finds the newest move, never a flag change."""
    from mailmind.bootstrap import mailbox
    from mailmind.bootstrap.mailbox import store
    pk = world.pk(RECEIPT)
    world.server.drop("SELECT", user=w.ADA)
    assert app.post("/api/archive", {"pk": pk}).json["status"] == "queued"
    world.server.drop("SELECT", user=w.ADA)
    assert app.post("/api/flag", {"pk": pk, "flag": "flagged", "on": True}).json["status"] == "queued"
    c = store.conn()
    top = c.execute("SELECT id, entry FROM undo_entries ORDER BY id DESC LIMIT 1").fetchone()
    c.execute("UPDATE undo_entries SET entry=json_remove(entry, '$.command') WHERE id=?", (top["id"],))
    c.commit()
    c.close()
    assert mailbox.undo.undo_last()[0]["ok"]
    assert [r["kind"] for r in rows()] == ["flag"]
    assert not hidden(pk)


def test_a_block_trashes_with_nothing_for_undo_to_reverse(app, world):
    from mailmind.bootstrap import mailbox
    assert mailbox.filing.trash_now(world.pk(RECEIPT)) is True
    assert undo_stack() == [] and boxes(world, RECEIPT) == ["Trash"]


def test_an_outage_of_hours_leaves_a_move_waiting_and_its_message_hidden(app, world):
    from datetime import timedelta

    from mailmind.bootstrap.mailbox import commands
    pk = world.pk(RECEIPT)
    world.server.drop("SELECT", user=w.ADA, times=None)
    assert app.post("/api/archive", {"pk": pk}).json["status"] == "queued"
    for _ in range(12):
        commands.drain()
        app.clock.shift(timedelta(seconds=commands._BACKOFF_CAP + 1))
    [row] = rows()
    assert (row["state"], row["attempts"]) == ("queued", 12) and hidden(pk)
    world.server.clear_faults()
    commands.drain()
    assert boxes(world, RECEIPT) == ["Archive"] and rows()[0]["state"] == "done"


def test_a_queued_change_is_given_up_on_after_about_a_day_of_tries(app, world):
    from mailmind.bootstrap.mailbox import commands
    hours = sum(commands.QUEUE.backoff(n) for n in range(1, commands.MAX_ATTEMPTS)) / 3600
    assert 23 < hours < 25


def test_a_message_says_which_account_it_is_in(world):
    """What a send's bookkeeping falls back on for a row that doesn't name its original's account."""
    from mailmind.bootstrap.mailbox import server_ops
    assert server_ops.account_of(world.pk(LUNCH)) == w.ADA
    assert server_ops.account_of(10 ** 9) is None
    assert server_ops.account_of(None) is None


def test_undo_of_a_queued_archive_whose_mail_left_on_its_own_stays_out_of_the_way(app, world, monkeypatch):
    """Nothing names where an archive that hasn't gone yet would have put it, so an entry like this could only ever fail,
    on top of everything beneath it, until it expired."""
    from mailmind.bootstrap import mailbox
    from mailmind.bootstrap.mailbox import server_ops, store
    until = int((datetime.now(timezone.utc) + timedelta(hours=6)).timestamp())
    assert app.post("/api/snooze", {"pk": world.pk(LUNCH), "until": until}).json["ok"]
    pk = world.pk(RECEIPT)
    with monkeypatch.context() as m:
        m.setattr(server_ops, "apply", dies_before)
        with pytest.raises(Crash):
            mailbox.filing.do_write("/api/archive", {"pk": pk})
    restart()
    assert not undo_stack()[-1].get("dest_folder")
    world.server.mailbox(*world.placed[RECEIPT][:2]).messages.pop(world.uid(RECEIPT))
    c = store.conn()
    c.execute("DELETE FROM messages WHERE pk=?", (pk,))
    c.commit()
    c.close()
    assert app.post("/api/undo", {}).json["ok"]
    assert app.post("/api/undo", {}).json["ok"]               # the snooze beneath it
    assert undo_stack() == []
