"""Moving mail to a folder a person picked: one message, a conversation or the marked rows, through the one command path, with
Undo, and only into the account's own folders that take filing. All addresses are fictional."""
import pytest

import problems
from fixtures import world as w
from gate import held_elsewhere
from mailmind.bootstrap import mailbox
from mailmind.mailbox.application.filing import Filing

LUNCH = "ada-inbox-01-lunch.eml"
CONFIRM = "ada-inbox-02-lunch-confirm.eml"
RECEIPT = "ada-inbox-05-receipt.eml"
NEWSLETTER = "ada-inbox-03-newsletter.eml"
CLIENT = "bea-inbox-01-client.eml"
SECURITY = "bea-inbox-02-security.eml"
CLIENTS = "Folders/Clients"
ADA_FOLDER = "Lists/Yarn & Wool"


@pytest.fixture
def ada_folder(world):
    world.server.add_folder(w.ADA, ADA_FOLDER)
    return ADA_FOLDER


def assert_is(r, problem):
    assert (r.status, r.json) == (problem["status"], problem)


def boxes(world, name):
    return [box for _, box, _, _ in world.where(name)["server"]]


def listed(app, account=w.BEA, folder="INBOX"):
    return {i["key"] for i in app.get(f"/api/messages?account={account}&folder={folder}&limit=500").json["items"]}


def move(app, world, name, to, **more):
    return app.post("/api/move", {**world.copy(name), "to": to, **more})


def thread_move(app, world, name, to, **more):
    return app.post("/api/thread/move", {**world.copy(name), "to": to, **more})


def bulk_move(app, to, *rows, **more):
    return app.post("/api/bulk/move", {"to": to, "rows": list(rows), **more})


def row(world, name, whole=False):
    return {**world.copy(name), "whole": whole}


def nothing_moved(world, name, mark):
    assert world.placed[name][1] in boxes(world, name) and world.writes(mark) == []
    assert mailbox.undo.entries() == []


def test_a_message_moves_to_the_folder_picked_and_undo_brings_it_back(app, world):
    a = move(app, world, CLIENT, CLIENTS).json
    assert (a["ok"], a["msg"], a["undoable"]) == (True, "moved", True)
    assert boxes(world, CLIENT) == [CLIENTS]
    assert world.key(CLIENT) not in listed(app)
    [entry] = mailbox.undo.entries()
    assert (entry["label"], entry["dest_folder"], entry["src_folder"]) == ("Moved", CLIENTS, "INBOX")
    u = app.post("/api/undo", {}).json
    assert (u["ok"], u["label"]) == (True, "Moved")
    assert boxes(world, CLIENT) == ["INBOX"]
    assert world.key(CLIENT) in listed(app)


def test_it_goes_as_a_move_of_the_one_uid_named(app, world):
    mark = len(world.server.commands)
    assert move(app, world, CLIENT, CLIENTS).status == 200
    assert world.writes(mark) == [f"{w.BEA} [INBOX] UID MOVE {world.uid(CLIENT)} \"{CLIENTS}\""]


def test_the_folder_opened_next_catches_up_at_once(app, world, monkeypatch):
    app.get(f"/api/messages?account={w.BEA}&folder={CLIENTS}&limit=50")     # read just now: a catch-up isn't due
    assert move(app, world, CLIENT, CLIENTS).status == 200
    assert world.key(CLIENT) in listed(app, folder=CLIENTS)


def test_any_of_the_folders_that_take_filing_is_one_a_message_can_go_to(app, world):
    """Archive and Trash by name as well as the verbs: the same folders, a person's choice."""
    assert move(app, world, RECEIPT, "Archive").json["msg"] == "moved"
    assert boxes(world, RECEIPT) == ["Archive"]
    assert move(app, world, NEWSLETTER, "Trash").json["msg"] == "moved"
    assert boxes(world, NEWSLETTER) == ["Trash"]


def test_mail_goes_back_to_the_inbox_as_well(app, world):
    r = move(app, world, "bea-clients-01-harbor.eml", "INBOX")
    assert r.json["msg"] == "moved" and boxes(world, "bea-clients-01-harbor.eml") == ["INBOX"]


@pytest.mark.parametrize("name, to, says", [
    (RECEIPT, CLIENTS, "no such folder on this account"),           # Bea's, not Ada's
    (RECEIPT, "No Such Folder", "no such folder on this account"),
    (RECEIPT, "__snoozed__", "no such folder on this account"),
    (RECEIPT, "__screener__", "no such folder on this account"),
    (RECEIPT, 'Archive"\r\nA1 LOGOUT', "no such folder on this account"),
    (RECEIPT, "Drafts", "mail can't be moved into that folder"),
    (RECEIPT, "Sent", "mail can't be moved into that folder"),
    ("cy-inbox-03-unrelated.eml", "[Gmail]/All Mail", "mail can't be moved into that folder"),
    (RECEIPT, "INBOX", "it's in that folder already"),
])
def test_it_moves_nothing_to_anywhere_but_a_folder_of_its_own_that_takes_filing(app, world, name, to, says):
    mark = len(world.server.commands)
    r = move(app, world, name, to)
    assert_is(r, problems.refused(says))
    nothing_moved(world, name, mark)
    assert world.key(name) in listed(app, world.placed[name][0])


def test_a_move_names_a_folder(app, world):
    r = app.post("/api/move", world.copy(RECEIPT))
    assert_is(r, problems.refused("bad request"))
    r = app.post("/api/move", {**world.copy(RECEIPT), "to": ""})
    assert_is(r, problems.refused("bad request"))


def test_a_message_that_has_gone_answers_not_found(app, world):
    r = app.post("/api/move", {"key": 999999, "folder": "INBOX", "to": CLIENTS})
    assert_is(r, problems.not_found("not found"))


def test_a_folder_refused_by_the_server_leaves_the_message_where_it_was(app, world):
    world.server.fail("UID MOVE", user=w.BEA)
    world.server.fail("UID COPY", user=w.BEA)
    r = move(app, world, CLIENT, CLIENTS)
    assert_is(r, problems.conflict("the server refused the move"))
    assert boxes(world, CLIENT) == ["INBOX"] and world.key(CLIENT) in listed(app)
    assert mailbox.undo.entries() == []


def test_a_server_away_queues_it_and_the_tray_says_where_it_goes(app, world):
    world.server.drop("SELECT", user=w.BEA)
    a = move(app, world, CLIENT, CLIENTS, mutation_id="m-1").json
    assert (a["status"], a["undoable"]) == ("queued", True)
    [item] = app.get("/api/pending").json["items"]
    assert (item["op"], item["dest"], item["folder"]) == ("move", CLIENTS, "INBOX")
    assert world.key(CLIENT) not in listed(app)
    mailbox.commands.drain()
    assert boxes(world, CLIENT) == [CLIENTS]


def test_undo_of_a_queued_move_cancels_it(app, world):
    world.server.drop("SELECT", user=w.BEA)
    assert move(app, world, CLIENT, CLIENTS).json["status"] == "queued"
    app.post("/api/undo", {})
    assert app.get("/api/pending").json["items"] == [] and world.key(CLIENT) in listed(app)
    assert boxes(world, CLIENT) == ["INBOX"]


def test_a_folder_made_since_the_last_list_is_found_by_asking(app, world):
    world.server.add_folder(w.BEA, "Folders/New")
    assert move(app, world, CLIENT, "Folders/New").json["msg"] == "moved"
    assert boxes(world, CLIENT) == ["Folders/New"]


def test_a_folder_known_already_costs_the_click_no_list(app, world):
    app.get(f"/api/folders?account={w.BEA}")
    with world.server.lock:
        before = len(world.server.commands)
    assert move(app, world, CLIENT, CLIENTS).status == 200
    assert not [c for c in world.server.commands[before:] if c["command"] == "LIST"]


def test_a_busy_gate_doesnt_hold_a_check_for_a_folder_nobody_knows(app, world):
    world.server.add_folder(w.BEA, "Folders/New")
    with held_elsewhere(w.BEA):
        r = move(app, world, CLIENT, "Folders/New")
    assert r.status == 409 and r.json["busy"] is True
    assert boxes(world, CLIENT) == ["INBOX"]


def test_the_folder_list_says_which_take_filing(app, world):
    folders = app.get(f"/api/folders?account={w.ADA}").json["folders"]
    takes = {f["name"]: f["takes_filing"] for f in folders}
    assert (takes["INBOX"], takes["Archive"], takes["Trash"], takes["Sent"], takes["Drafts"]) == (True, True, True, False, False)


# ── a whole conversation ─────────────────────────────────────────────────────────────────────────────────────────────


def test_a_conversation_moves_whole_and_one_undo_brings_it_all_back(app, world, ada_folder):
    a = thread_move(app, world, CONFIRM, ada_folder).json
    assert (a["ok"], a["count"], a["failed"], a["undoable"]) == (True, 2, 0, True)
    assert boxes(world, LUNCH) == [ada_folder] and boxes(world, CONFIRM) == [ada_folder]
    assert len(mailbox.undo.entries()) == 1
    u = app.post("/api/undo", {}).json
    assert (u["reversed"], u["failed"]) == (2, 0)
    assert boxes(world, LUNCH) == ["INBOX"] and boxes(world, CONFIRM) == ["INBOX"]


def test_a_conversation_is_held_to_the_same_folders(app, world):
    mark = len(world.server.commands)
    assert_is(thread_move(app, world, CONFIRM, CLIENTS), problems.refused("no such folder on this account"))
    assert_is(thread_move(app, world, CONFIRM, "Drafts"), problems.refused("mail can't be moved into that folder"))
    assert world.writes(mark) == [] and mailbox.undo.entries() == []


# ── marked rows ──────────────────────────────────────────────────────────────────────────────────────────────────────


def test_marked_rows_move_and_one_undo_brings_them_all_back(app, world):
    a = bulk_move(app, CLIENTS, row(world, CLIENT), row(world, SECURITY), undo_group="ug-1").json
    assert (a["ok"], a["count"], a["failed"], a["rows_failed"], a["partial"], a["undoable"]) == (True, 2, 0, 0, False, True)
    assert boxes(world, CLIENT) == [CLIENTS] and boxes(world, SECURITY) == [CLIENTS]
    assert len(mailbox.undo.entries()) == 1
    u = app.post("/api/undo", {}).json
    assert (u["reversed"], u["failed"]) == (2, 0)
    assert boxes(world, CLIENT) == ["INBOX"] and boxes(world, SECURITY) == ["INBOX"]


def test_a_whole_row_moves_its_conversation(app, world, ada_folder):
    a = bulk_move(app, ada_folder, row(world, CONFIRM, whole=True)).json
    assert a["count"] == 2 and boxes(world, LUNCH) == [ada_folder]


def test_rows_of_two_accounts_are_refused_before_any_moves(app, world):
    mark = len(world.server.commands)
    r = bulk_move(app, CLIENTS, row(world, CLIENT), row(world, RECEIPT))
    assert_is(r, problems.refused("those are in different accounts"))
    assert world.writes(mark) == [] and mailbox.undo.entries() == []
    assert boxes(world, CLIENT) == ["INBOX"] and boxes(world, RECEIPT) == ["INBOX"]


def test_marked_rows_are_held_to_the_accounts_own_folders(app, world):
    mark = len(world.server.commands)
    assert_is(bulk_move(app, "Archive2", row(world, RECEIPT)), problems.refused("no such folder on this account"))
    assert_is(bulk_move(app, "Drafts", row(world, RECEIPT)), problems.refused("mail can't be moved into that folder"))
    assert world.writes(mark) == []


def test_a_row_already_in_that_folder_counts_as_not_moved_and_the_rest_go(app, world):
    a = bulk_move(app, CLIENTS, row(world, CLIENT), row(world, "bea-clients-01-harbor.eml")).json
    assert (a["count"], a["rows_failed"]) == (1, 1)
    assert boxes(world, CLIENT) == [CLIENTS]
    assert boxes(world, "bea-clients-01-harbor.eml") == [CLIENTS]


def test_a_copy_that_has_gone_is_counted_and_all_gone_is_not_found(app, world):
    r = bulk_move(app, CLIENTS, {"key": 999999, "folder": "INBOX"})
    assert_is(r, problems.not_found("not found"))


def test_marked_rows_are_held_to_one_requests_size(app, world):
    r = bulk_move(app, CLIENTS, *[row(world, CLIENT)] * (Filing.BULK_MAX + 1))
    assert r.status == 400 and boxes(world, CLIENT) == ["INBOX"]
