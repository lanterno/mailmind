"""Marked rows filed as one request: accepted in one transaction under one undo group, each then tried on its own, for about
as long as one click waits."""
import pytest

from fixtures import world as w
from gate import held_elsewhere
from mailmind.bootstrap import mailbox

LUNCH, CONFIRM = "ada-inbox-01-lunch.eml", "ada-inbox-02-lunch-confirm.eml"
RECEIPT, NEWSLETTER = "ada-inbox-05-receipt.eml", "ada-inbox-03-newsletter.eml"
GMAIL = "cy-inbox-02-thread-end.eml"


def file(app, verb, *rows, **more):
    return app.post("/api/bulk/file", {"verb": verb, "rows": list(rows), **more})


def row(world, name, whole=False):
    return {**world.copy(name), "whole": whole}


def at(world, name):
    """The copy the corpus put `name` in, as `listed` gives one."""
    copy = world.copy(name)
    return copy["key"], copy["folder"]


def listed(app, email=w.ADA):
    """Each copy the Inbox lists, by its key and folder."""
    return {(i["key"], i["folder"]) for i in app.get(f"/api/messages?account={email}&folder=INBOX&limit=500").json["items"]}


def folders_of(world, name):
    return [box for _, box, _, _ in world.where(name)["server"]]


def refuse_the_next_move(world):
    world.server.fail("UID MOVE", user=w.ADA)
    world.server.fail("UID COPY", user=w.ADA)


def test_it_files_each_row_and_one_undo_brings_them_all_back(app, world):
    copies = {at(world, RECEIPT), at(world, NEWSLETTER)}
    a = file(app, "archive", row(world, RECEIPT), row(world, NEWSLETTER), undo_group="ug-1").json
    assert (a["ok"], a["count"], a["failed"], a["rows_failed"], a["partial"], a["undoable"]) == (True, 2, 0, 0, False, True)
    assert not copies & listed(app)
    assert "INBOX" not in folders_of(world, RECEIPT) and "INBOX" not in folders_of(world, NEWSLETTER)
    assert len(mailbox.undo.entries()) == 1
    u = app.post("/api/undo", {}).json
    assert (u["ok"], u["reversed"], u["failed"]) == (True, 2, 0)
    assert "INBOX" in folders_of(world, RECEIPT) and "INBOX" in folders_of(world, NEWSLETTER)


def test_a_whole_row_files_its_conversation_in_its_folder(app, world):
    a = file(app, "trash", row(world, CONFIRM, whole=True)).json
    assert a["count"] == 2 and a["rows_failed"] == 0
    assert not {at(world, LUNCH), at(world, CONFIRM)} & listed(app)


def test_two_rows_of_one_conversation_count_once(app, world):
    a = file(app, "archive", row(world, LUNCH, whole=True), row(world, CONFIRM, whole=True)).json
    assert (a["count"], a["failed"], a["rows_failed"]) == (2, 0, 0)


def test_junk_never_stands_for_a_conversation(app, world):
    a = file(app, "junk", row(world, CONFIRM, whole=True)).json
    assert a["count"] == 1
    assert at(world, LUNCH) in listed(app)


def test_rows_of_several_accounts_each_go_to_their_own_server(app, world):
    a = file(app, "archive", row(world, RECEIPT), row(world, GMAIL)).json
    assert (a["count"], a["rows_failed"]) == (2, 0)
    assert at(world, RECEIPT) not in listed(app) and at(world, GMAIL) not in listed(app, w.CY)


def test_a_row_that_is_gone_fails_alone(app, world):
    a = file(app, "archive", {"key": 999_999, "folder": "INBOX"}, row(world, RECEIPT)).json
    assert (a["ok"], a["count"], a["failed"], a["rows_failed"]) == (True, 1, 0, 1)    # a copy gone is no message tried
    assert a["filed"] == [world.copy(RECEIPT)]


def test_one_the_server_refuses_goes_back_alone(app, world):
    keep, go = at(world, RECEIPT), at(world, NEWSLETTER)
    refuse_the_next_move(world)
    a = file(app, "archive", row(world, RECEIPT), row(world, NEWSLETTER)).json
    assert (a["ok"], a["count"], a["failed"], a["rows_failed"]) == (True, 1, 1, 1)
    assert keep in listed(app) and go not in listed(app)


def test_a_conversation_the_server_refuses_in_part_is_partial(app, world):
    refuse_the_next_move(world)
    a = file(app, "archive", row(world, CONFIRM, whole=True)).json
    assert (a["count"], a["failed"], a["rows_failed"], a["partial"]) == (1, 1, 0, True)


def test_a_failure_while_accepting_leaves_all_of_it_as_it_was(app, world, monkeypatch):
    real = mailbox.commands.accept_move_within
    went = []

    def second_fails(c, kind, pk, *rest):
        if went:
            raise RuntimeError("the machine stopped here")
        went.append(pk)
        return real(c, kind, pk, *rest)
    monkeypatch.setattr(mailbox.commands, "accept_move_within", second_fails)
    before = listed(app)
    assert file(app, "archive", row(world, RECEIPT), row(world, NEWSLETTER)).status == 500
    monkeypatch.undo()
    assert listed(app) == before
    assert mailbox.undo.entries() == []


def test_a_click_waits_about_as_long_as_for_one_and_the_worker_has_the_rest(app, world, monkeypatch):
    monkeypatch.setattr(mailbox.commands, "FILE_WAIT", 0)
    since = world.mark()
    a = file(app, "archive", row(world, RECEIPT), row(world, NEWSLETTER), row(world, CONFIRM)).json
    assert (a["ok"], a["count"], a["undoable"]) == (True, 3, True)
    assert world.writes(since) and "INBOX" not in folders_of(world, RECEIPT)                  # the first was tried
    assert "INBOX" in folders_of(world, NEWSLETTER) and "INBOX" in folders_of(world, CONFIRM)
    assert not {at(world, RECEIPT), at(world, NEWSLETTER), at(world, CONFIRM)} & listed(app)  # they left the list together
    mailbox.commands.drain()
    for name in (RECEIPT, NEWSLETTER, CONFIRM):
        assert "INBOX" not in folders_of(world, name)
    assert app.post("/api/undo", {}).json["reversed"] == 3


def test_a_busy_gate_costs_one_wait_and_each_stays_filed(app, world, monkeypatch):
    monkeypatch.setattr(mailbox.commands, "FILE_WAIT", 0.05)
    with held_elsewhere(w.ADA):
        a = file(app, "archive", row(world, RECEIPT), row(world, NEWSLETTER)).json
    assert (a["ok"], a["count"], a["failed"]) == (True, 2, 0)
    mailbox.commands.drain()
    assert "INBOX" not in folders_of(world, RECEIPT) and "INBOX" not in folders_of(world, NEWSLETTER)


@pytest.mark.parametrize("body", [
    {"verb": "delete", "rows": []},
    {"verb": "archive", "rows": "1,2"},
    {"verb": "archive", "rows": [1]},
    {"verb": "archive", "rows": [{"key": "x", "folder": "INBOX"}]},
    {"verb": "archive"},
    {"verb": "archive", "rows": [{"key": 1, "folder": "INBOX"}] * 501},
], ids=["unknown verb", "not a list", "not a row", "no number", "no rows", "too many"])
def test_what_it_refuses_changes_nothing(app, body):
    assert app.post("/api/bulk/file", body).status == 400
    assert mailbox.undo.entries() == []
