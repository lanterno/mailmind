"""Filing a whole conversation (/api/thread/archive and /api/thread/trash): which messages it takes,
one Undo for all of them, and what a failure leaves, on the fake IMAP server."""
import pytest

import problems
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


def in_order(copies):
    return sorted(copies, key=lambda c: (c["key"], c["folder"]))


def test_files_every_message_of_the_thread_in_the_anchors_folder(app, world):
    copies = in_order([world.copy(LUNCH), world.copy(CONFIRM)])
    set_thread(world, CLIENT, world.row(LUNCH)["thread_id"])   # the same id on another account: not this one
    r = app.post("/api/thread/archive", world.copy(CONFIRM)).json
    assert (r["ok"], r["count"], r["failed"], in_order(r["filed"])) == (True, 2, 0, copies)
    assert boxes(world, LUNCH) == ["Archive"] and boxes(world, CONFIRM) == ["Archive"]
    assert boxes(world, REPLY) == ["Sent"]                     # your reply lives in Sent: not this row's
    assert boxes(world, RECEIPT) == ["INBOX"]                  # a different conversation
    assert boxes(world, CLIENT) == ["INBOX"]


def test_any_message_can_anchor_the_conversation(app, world):
    copies = in_order([world.copy(LUNCH), world.copy(CONFIRM)])
    r = app.post("/api/thread/trash", world.copy(LUNCH)).json
    assert in_order(r["filed"]) == copies


def test_a_message_without_a_thread_is_a_conversation_of_one(app, world):
    set_thread(world, RECEIPT, None)
    set_thread(world, CI, "")
    copy = world.copy(RECEIPT)
    r = app.post("/api/thread/archive", copy).json
    assert (r["filed"], r["count"]) == ([copy], 1)
    assert boxes(world, RECEIPT) == ["Archive"] and boxes(world, CI) == ["INBOX"]


def test_the_trash_route_uses_trash(app, world):
    r = app.post("/api/thread/trash", world.copy(CONFIRM)).json
    assert r["ok"] and boxes(world, LUNCH) == ["Trash"] and boxes(world, CONFIRM) == ["Trash"]
    assert stack()[-1]["label"] == "Trashed"


def test_one_undo_entry_for_the_whole_conversation(app, world):
    r = app.post("/api/thread/archive", world.copy(CONFIRM)).json
    [entry] = stack()
    assert r["undoable"] and entry["kind"] == "bulk" and len(entry["children"]) == 2
    assert app.post("/api/undo", {}).json["ok"]
    assert boxes(world, LUNCH) == ["INBOX"] and boxes(world, CONFIRM) == ["INBOX"]


def test_a_bulk_selection_shares_the_callers_undo_group(app, world):
    app.post("/api/thread/archive", {**world.copy(CONFIRM), "undo_group": "g-1"})
    app.post("/api/thread/archive", {**world.copy(RECEIPT), "undo_group": "g-1"})
    [entry] = stack()
    assert len(entry["children"]) == 3


def test_a_partial_failure_reports_what_was_filed(app, world):
    refuse_moves(world, LUNCH)
    confirm = world.copy(CONFIRM)
    r = app.post("/api/thread/archive", confirm).json
    assert (r["ok"], r["count"], r["failed"], r["filed"]) == (True, 1, 1, [confirm])
    assert boxes(world, LUNCH) == ["INBOX"] and boxes(world, CONFIRM) == ["Archive"]
    assert world.row(LUNCH)["folder"] == "INBOX"


def test_a_total_failure_is_the_first_refusal_and_leaves_nothing_to_undo(app, world):
    refuse_moves(world)
    r = app.post("/api/thread/archive", world.copy(CONFIRM))
    assert (r.status, r.json) == (409, problems.conflict("the server refused the archive"))
    assert boxes(world, LUNCH) == ["INBOX"] and world.row(LUNCH)["folder"] == "INBOX"
    assert stack() == []


def test_an_unreachable_server_queues_each_message(app, world):
    from mailmind.bootstrap.mailbox import store
    world.server.drop("SELECT", user=w.ADA, times=None)
    r = app.post("/api/thread/trash", world.copy(CONFIRM)).json
    assert (r["ok"], r["count"], r["queued"]) == (True, 2, 2)
    c = store.conn()
    queued = c.execute("SELECT COUNT(*) FROM mail_commands WHERE kind='trash' AND applied=0").fetchone()[0]
    c.close()
    assert queued == 2
    assert world.where(LUNCH)["db"] == [] and world.where(CONFIRM)["db"] == []   # out of the lists


def test_a_missing_or_bad_anchor(app):
    assert app.post("/api/thread/archive", {"key": 999999, "folder": "INBOX"}).json == problems.not_found("not found")
    assert app.post("/api/thread/archive", {"key": "x", "folder": "INBOX"}).json["detail"] == "bad request"
    assert app.post("/api/thread/archive", {}).json == problems.BAD_REQUEST          # a key and folder are the schema's to require


# -- one transaction accepts them all, and each is tried by itself ------------

class Crash(BaseException):
    """The process died here: not an Exception, so no handler on the way out takes it for a failure."""


def commands():
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return [dict(r) for r in c.execute("SELECT * FROM mail_commands ORDER BY id")]
    finally:
        c.close()


def hidden():
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return [r["pk"] for r in c.execute("SELECT pk FROM messages WHERE pending_removal IS NOT NULL")]
    finally:
        c.close()


def restart():
    from mailmind.bootstrap.mailbox import commands as path, store
    c = store.conn()
    path.QUEUE.reset_stuck(c)
    c.commit()
    c.close()


def test_the_whole_conversation_leaves_the_lists_before_the_first_try(app, world, monkeypatch):
    from mailmind.bootstrap.mailbox import server_ops
    real, listed = server_ops.apply, []

    def looks(row, first_try=False):
        listed.append([world.where(n)["db"] for n in (LUNCH, CONFIRM)])
        return real(row, first_try)
    monkeypatch.setattr(server_ops, "apply", looks)
    assert app.post("/api/thread/archive", world.copy(CONFIRM)).json["count"] == 2
    assert listed[0] == [[], []]


def test_a_crash_while_accepting_leaves_all_of_it_as_it_was(world, monkeypatch):
    from mailmind.bootstrap import mailbox
    real, pushed = mailbox.undo.push_within, []

    def dies_at_the_second(c, entry, group=None):
        pushed.append(entry)
        if len(pushed) == 2:
            raise Crash
        real(c, entry, group)
    monkeypatch.setattr(mailbox.undo, "push_within", dies_at_the_second)
    since = world.mark()
    with pytest.raises(Crash):
        mailbox.filing.do_thread_file("/api/thread/archive", {"pk": world.pk(CONFIRM)})
    assert (commands(), hidden(), stack()) == ([], [], [])
    assert world.writes(since) == []
    assert boxes(world, LUNCH) == ["INBOX"] and world.where(LUNCH)["db"]


def test_a_crash_in_the_first_try_leaves_the_rest_for_the_next_start(world, monkeypatch):
    from mailmind.bootstrap import mailbox

    def dies(row, first_try=False):
        raise Crash
    with monkeypatch.context() as m:
        m.setattr(mailbox.server_ops, "apply", dies)
        with pytest.raises(Crash):
            mailbox.filing.do_thread_file("/api/thread/archive", {"pk": world.pk(CONFIRM)})
    assert [r["state"] for r in commands()] == ["running", "running"]
    assert sorted(hidden()) == sorted([world.pk(LUNCH), world.pk(CONFIRM)])
    assert len(stack()[0]["children"]) == 2
    restart()
    mailbox.commands.drain()
    assert [r["state"] for r in commands()] == ["done", "done"]
    assert boxes(world, LUNCH) == ["Archive"] and boxes(world, CONFIRM) == ["Archive"]


def test_a_try_that_raises_leaves_it_queued_and_the_rest_still_go(world, monkeypatch):
    from mailmind.bootstrap import mailbox
    real, tried = mailbox.server_ops.apply, []

    def first_breaks(row, first_try=False):
        tried.append(row["message_id"])
        if len(tried) == 1:
            raise RuntimeError("something nobody expected")
        return real(row, first_try)
    monkeypatch.setattr(mailbox.server_ops, "apply", first_breaks)
    r = mailbox.filing.do_thread_file("/api/thread/archive", {"pk": world.pk(CONFIRM)})
    assert (r["ok"], r["count"], r["failed"], r["queued"], r["undoable"]) == (True, 2, 0, 1, True)
    assert len(set(tried)) == 2
    assert sorted(x["state"] for x in commands()) == ["done", "queued"]     # nothing left to wait for a restart
    mailbox.commands.drain()
    assert boxes(world, LUNCH) == ["Archive"] and boxes(world, CONFIRM) == ["Archive"]


def test_a_refusal_gives_back_only_its_own_message(app, world):
    refuse_moves(world, LUNCH)
    r = app.post("/api/thread/archive", world.copy(CONFIRM)).json
    assert (r["count"], r["failed"]) == (1, 1)
    assert hidden() == [] and world.where(LUNCH)["db"] and world.where(CONFIRM)["db"] == []
    assert [x["message_id"] for x in commands()] == [w.message_id(CONFIRM)]
    [entry] = stack()
    assert [d["message_id"] for d in entry["children"]] == [w.message_id(CONFIRM)]
    world.server.clear_faults()
    assert app.post("/api/undo", {}).json["reversed"] == 1
    assert boxes(world, CONFIRM) == ["INBOX"] and boxes(world, LUNCH) == ["INBOX"]


def test_an_undo_before_any_try_landed_lists_all_of_it_again(app, world):
    from mailmind.bootstrap.mailbox import commands as path
    world.server.drop("SELECT", user=w.ADA, times=None)
    assert app.post("/api/thread/archive", world.copy(CONFIRM)).json["queued"] == 2
    since = world.mark()
    r = app.post("/api/undo", {}).json
    assert (r["ok"], r["reversed"], r["failed"]) == (True, 2, 0)
    assert (commands(), hidden(), stack()) == ([], [], [])
    world.server.clear_faults()
    path.drain()
    assert world.writes(since) == [] and boxes(world, LUNCH) == ["INBOX"] and world.where(CONFIRM)["db"]


def test_a_gmail_conversation_loses_its_inbox_label_and_gets_it_back(app, world):
    start, end = "cy-inbox-01-thread-start.eml", "cy-inbox-02-thread-end.eml"
    r = app.post("/api/thread/archive", world.copy(end)).json
    assert (r["ok"], r["count"], r["failed"]) == (True, 2, 0)
    assert "INBOX" not in boxes(world, start) and "INBOX" not in boxes(world, end)
    assert "[Gmail]/All Mail" in boxes(world, start) and "[Gmail]/All Mail" in boxes(world, end)
    [entry] = stack()
    assert [(d["kind"], d["dest_folder"]) for d in entry["children"]] == [("gmail_archive", "")] * 2
    assert app.post("/api/undo", {}).json["reversed"] == 2
    assert "INBOX" in boxes(world, start) and "INBOX" in boxes(world, end)


def test_a_conversations_click_waits_about_as_long_as_for_one_message_and_the_worker_has_the_rest(app, world, monkeypatch):
    from mailmind.bootstrap import mailbox
    monkeypatch.setattr(mailbox.commands, "FILE_WAIT", 0)
    since = world.mark()
    a = app.post("/api/thread/archive", world.copy(CONFIRM)).json
    assert (a["ok"], a["count"], a["failed"]) == (True, 2, 0)
    tried = [name for name in (LUNCH, CONFIRM) if "INBOX" not in boxes(world, name)]
    assert len(tried) == 1 and world.writes(since)
    mailbox.commands.drain()
    assert all("INBOX" not in boxes(world, name) for name in (LUNCH, CONFIRM))
