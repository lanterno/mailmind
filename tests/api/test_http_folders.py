"""A folder a person makes, renames or deletes, over HTTP against the fake server: /api/folders/create, /api/folders/rename and
/api/folders/delete. Each pins what the server holds afterwards and what the rail and the lists say, and that nothing reached
the server where the change was refused.

Ada's plain IMAP host separates a path with "/", as Bea's Proton Bridge does, whose folders of her own sit in Folders/, the
one place Bridge makes one. Cy's Gmail keeps its own folders in [Gmail], and a folder of Cy's is a label.
"""
import urllib.parse

import problems
from fixtures import world as w

FOLDER_WORDS = {
    "no name": "A folder needs a name.",
    "nested": "A folder’s name can’t hold the server’s separator, /.",
    "taken": "There’s a folder by that name already.",
    "gone": "That folder isn’t on the server any more.",
    "plays a part": "MailMind doesn’t rename or delete the Inbox, Sent, Drafts, Archive, Junk or Trash.",
    "has folders": "This folder has folders inside it. Delete or move those first.",
    "server said no": "The server didn’t make that change.",
    "servers own": "The mail server keeps this folder, so MailMind doesn’t rename or delete it.",
}
CLIENTS = "bea-clients-01-harbor.eml"
UNRELATED = "cy-inbox-03-unrelated.eml"
LABELLED = "<labelled@example.test>"
OLD = "<old-folder@example.test>"


def on_server(world, user):
    with world.server.lock:
        return sorted(world.server.accounts[user].mailboxes)


def on_rail(app, user):
    return [f["name"] for f in app.get(f"/api/folders?account={user}").json["folders"]]


def rail(app, user):
    return {f["name"]: f for f in app.get(f"/api/folders?account={user}").json["folders"]}


def listed(app, user, folder):
    r = app.get("/api/messages?" + urllib.parse.urlencode({"account": user, "folder": folder}))
    return [row["subject"] for row in r.json["items"]]


def test_a_new_folder_is_made_on_the_server_and_shows_on_the_rail(app, world):
    r = app.post("/api/folders/create", {"account": w.ADA, "name": "  Receipts "})
    assert (r.status, r.json) == (200, {"ok": True, "folder": "Receipts"})
    assert "Receipts" in on_server(world, w.ADA)
    assert "Receipts" in on_rail(app, w.ADA)


def test_a_name_beyond_ascii_goes_as_imap_names_it(app, world):
    r = app.post("/api/folders/create", {"account": w.ADA, "name": "Été & co"})
    assert (r.status, r.json) == (200, {"ok": True, "folder": "&AMk-t&AOk- &- co"})
    assert "&AMk-t&AOk- &- co" in on_server(world, w.ADA)


def test_a_name_that_cant_be_one_is_refused_before_the_server_hears_of_it(app, world):
    since = world.mark()
    for body, words in (({"account": w.ADA, "name": "  "}, "no name"), ({"account": w.ADA}, "no name"),
                        ({"account": w.ADA, "name": "Work/Clients"}, "nested")):
        r = app.post("/api/folders/create", body)
        assert (r.status, r.json) == (400, problems.refused(FOLDER_WORDS[words]))
    r = app.post("/api/folders/create", {"account": w.ADA, "name": "Archive"})
    assert (r.status, r.json) == (409, problems.conflict(FOLDER_WORDS["taken"]))
    assert world.writes(since) == []
    r = app.post("/api/folders/create", {"name": "Receipts"})
    assert (r.status, r.json) == (400, problems.refused("account required"))


def test_a_new_folder_on_proton_goes_in_folders_where_bridge_makes_one(app, world):
    r = app.post("/api/folders/create", {"account": w.BEA, "name": "Receipts"})
    assert (r.status, r.json) == (200, {"ok": True, "folder": "Folders/Receipts"})
    assert "Folders/Receipts" in on_server(world, w.BEA)
    assert rail(app, w.BEA)["Folders/Receipts"]["own_name"] == "Receipts"


def test_a_folder_the_server_wont_make_is_said_so(app, world):
    world.server.fail("CREATE", "NO [CANNOT] not today")
    r = app.post("/api/folders/create", {"account": w.ADA, "name": "Receipts"})
    assert (r.status, r.json) == (409, problems.conflict(FOLDER_WORDS["server said no"]))
    assert "Receipts" not in on_server(world, w.ADA)


def test_a_renamed_folder_keeps_its_place_and_its_mail(app, world):
    subjects = listed(app, w.BEA, "Folders/Clients")
    assert subjects
    r = app.post("/api/folders/rename", {"account": w.BEA, "folder": "Folders/Clients", "name": "Customers"})
    assert (r.status, r.json) == (200, {"ok": True, "folder": "Folders/Customers"})
    names = on_server(world, w.BEA)
    assert "Folders/Customers" in names and "Folders/Clients" not in names
    assert len(world.server.messages(w.BEA, "Folders/Customers")) == 1
    rail = on_rail(app, w.BEA)
    assert "Folders/Customers" in rail and "Folders/Clients" not in rail
    assert listed(app, w.BEA, "Folders/Customers") == subjects


def test_the_folders_that_play_a_part_are_never_renamed_or_deleted(app, world):
    since = world.mark()
    for folder in ("INBOX", "inbox", "Sent", "Drafts", "Trash", "Junk", "Archive"):
        r = app.post("/api/folders/rename", {"account": w.ADA, "folder": folder, "name": "Mine"})
        assert (r.status, r.json) == (400, problems.refused(FOLDER_WORDS["plays a part"])), folder
        r = app.post("/api/folders/delete", {"account": w.ADA, "folder": folder, "confirm": True})
        assert (r.status, r.json) == (400, problems.refused(FOLDER_WORDS["plays a part"])), folder
    assert world.writes(since) == []


def test_the_folders_the_server_keeps_are_never_renamed_or_deleted(app, world):
    world.server.add_folder(w.CY, "[Gmail]/Important", "\\Important")
    since = world.mark()
    for folder in ("[Gmail]/Starred", "[Gmail]/Important"):
        r = app.post("/api/folders/rename", {"account": w.CY, "folder": folder, "name": "Mine"})
        assert (r.status, r.json) == (400, problems.refused(FOLDER_WORDS["servers own"])), folder
        r = app.post("/api/folders/delete", {"account": w.CY, "folder": folder, "confirm": True})
        assert (r.status, r.json) == (400, problems.refused(FOLDER_WORDS["servers own"])), folder
    assert world.writes(since) == []


def test_the_rail_says_which_folders_a_person_may_change_and_each_ones_own_name(app, world):
    world.server.add_folder(w.CY, "[Gmail]/Important", "\\Important")
    world.server.add_folder(w.CY, "Work/&AMk-t&AOk-")
    folders = rail(app, w.CY)
    assert {name for name, f in folders.items() if f["changeable"]} == {"Work/&AMk-t&AOk-"}
    assert folders["Work/&AMk-t&AOk-"]["own_name"] == "Été"
    assert folders["[Gmail]/Starred"]["own_name"] == "Starred"
    assert {name for name, f in rail(app, w.BEA).items() if f["changeable"]} == {"Folders/Clients", "Labels/Important"}


def test_a_folder_renamed_to_the_name_it_has_is_left_as_it_is(app, world):
    world.server.add_folder(w.ADA, "R&-D")
    assert rail(app, w.ADA)["R&-D"]["own_name"] == "R&D"
    since = world.mark()
    r = app.post("/api/folders/rename", {"account": w.ADA, "folder": "R&-D", "name": "R&D"})
    assert (r.status, r.json) == (200, {"ok": True, "folder": "R&-D"})
    assert world.writes(since) == []
    assert "R&-D" in on_server(world, w.ADA)


def test_a_folder_not_on_the_server_or_a_name_taken_is_said_so(app, world):
    r = app.post("/api/folders/rename", {"account": w.ADA, "folder": "Nowhere", "name": "Mine"})
    assert (r.status, r.json) == (404, problems.not_found(FOLDER_WORDS["gone"]))
    r = app.post("/api/folders/delete", {"account": w.ADA, "folder": "Nowhere"})
    assert (r.status, r.json) == (404, problems.not_found(FOLDER_WORDS["gone"]))
    app.post("/api/folders/create", {"account": w.ADA, "name": "Old"})
    r = app.post("/api/folders/rename", {"account": w.ADA, "folder": "Old", "name": "Archive"})
    assert (r.status, r.json) == (409, problems.conflict(FOLDER_WORDS["taken"]))


def test_an_empty_folder_is_deleted_at_once(app, world):
    app.post("/api/folders/create", {"account": w.ADA, "name": "Old"})
    r = app.post("/api/folders/delete", {"account": w.ADA, "folder": "Old"})
    assert (r.status, r.json) == (200, {"ok": True, "trashed": 0})
    assert "Old" not in on_server(world, w.ADA)
    assert "Old" not in on_rail(app, w.ADA)


def test_a_folder_holding_mail_is_deleted_only_once_a_person_says_so_and_its_mail_goes_to_trash_first(app, world):
    since = world.mark()
    r = app.post("/api/folders/delete", {"account": w.BEA, "folder": "Folders/Clients"})
    assert (r.status, r.json) == (409, problems.conflict(
        "This folder holds 1 message. Deleting it moves them to Trash first.", messages=1, to_trash=True))
    assert world.writes(since) == []
    assert "Folders/Clients" in on_server(world, w.BEA)

    r = app.post("/api/folders/delete", {"account": w.BEA, "folder": "Folders/Clients", "confirm": True})
    assert (r.status, r.json) == (200, {"ok": True, "trashed": 1})
    assert "Folders/Clients" not in on_server(world, w.BEA)
    assert world.server.deleted_holding == []
    assert [server[:2] for server in world.where(CLIENTS)["server"]] == [[w.BEA, "Trash"]]
    assert not any(c.split(" ")[-1] == "EXPUNGE" for c in world.writes(since))
    assert "Folders/Clients" not in on_rail(app, w.BEA)


def test_a_deleted_folders_mail_goes_to_the_trash_the_server_lists(app, world):
    # Ada's server keeps its Trash as "Deleted Messages", as iCloud does: the folder's mail goes there, as a trash's does.
    with world.server.lock:
        boxes = world.server.accounts[w.ADA].mailboxes
        trash = boxes.pop("Trash")
        trash.name = "Deleted Messages"
        boxes[trash.name] = trash
    app.post("/api/folders/create", {"account": w.ADA, "name": "Old"})
    world.server.deliver(w.ADA, "Old", w.variant("ada-inbox-05-receipt.eml", Message_ID=OLD))
    r = app.post("/api/folders/delete", {"account": w.ADA, "folder": "Old", "confirm": True})
    assert (r.status, r.json) == (200, {"ok": True, "trashed": 1})
    assert world.server.find(w.ADA, "Deleted Messages", OLD)
    assert "Old" not in on_server(world, w.ADA) and "Trash" not in on_server(world, w.ADA)


def test_a_gmail_label_goes_and_its_mail_stays_in_all_mail(app, world):
    world.server.add_folder(w.CY, "Receipts")
    world.server.deliver(w.CY, "Receipts", w.variant(UNRELATED, Message_ID=LABELLED))
    held = world.server.find(w.CY, "[Gmail]/All Mail", LABELLED)
    assert held
    since = world.mark()
    r = app.post("/api/folders/delete", {"account": w.CY, "folder": "Receipts"})
    assert (r.status, r.json) == (409, problems.conflict(
        "This folder holds 1 message. Deleting it leaves them in All Mail.", messages=1, to_trash=False))
    assert world.writes(since) == []

    r = app.post("/api/folders/delete", {"account": w.CY, "folder": "Receipts", "confirm": True})
    assert (r.status, r.json) == (200, {"ok": True, "trashed": 0})
    assert "Receipts" not in on_server(world, w.CY)
    assert [c.split("] ", 1)[1].split(" ")[0] for c in world.writes(since)] == ["DELETE"]
    assert world.server.find(w.CY, "[Gmail]/All Mail", LABELLED) == held
    assert world.server.messages(w.CY, "[Gmail]/Trash") == {}


def test_a_delete_the_server_refuses_once_the_mail_has_gone_says_where_it_went(app, world):
    world.server.fail("DELETE", "NO [CANNOT] not today")
    r = app.post("/api/folders/delete", {"account": w.BEA, "folder": "Folders/Clients", "confirm": True})
    assert (r.status, r.json) == (409, problems.conflict(
        "Moved 1 message to Trash, but the server kept the folder.", trashed=1))
    assert "Folders/Clients" in on_server(world, w.BEA)
    assert [server[:2] for server in world.where(CLIENTS)["server"]] == [[w.BEA, "Trash"]]
    assert listed(app, w.BEA, "Folders/Clients") == []


def test_a_delete_cut_off_once_the_mail_was_on_its_way_says_it_may_be_in_trash(app, world):
    world.server.drop("UID MOVE")
    r = app.post("/api/folders/delete", {"account": w.BEA, "folder": "Folders/Clients", "confirm": True})
    assert (r.status, r.json) == (502, problems.unreachable(
        "The server stopped answering, so some of this folder’s mail may be in Trash already. Try again in a moment."))
    assert "Folders/Clients" in on_server(world, w.BEA)


def test_read_only_no_folder_is_made_renamed_or_deleted(app, world):
    app.env["MAILMIND_PROFILE"] = "read-only"
    app.restart()
    since = world.mark()
    for path, body in (("/api/folders/create", {"account": w.ADA, "name": "Receipts"}),
                       ("/api/folders/rename", {"account": w.BEA, "folder": "Folders/Clients", "name": "Customers"}),
                       ("/api/folders/delete", {"account": w.BEA, "folder": "Folders/Clients", "confirm": True})):
        r = app.post(path, body)
        assert (r.status, r.json) == (409, problems.read_only()), path
    assert world.writes(since) == []
    assert "Folders/Clients" in on_server(world, w.BEA) and "Receipts" not in on_server(world, w.ADA)


def test_a_folder_with_folders_inside_isnt_deleted(app, world):
    app.post("/api/folders/create", {"account": w.ADA, "name": "Projects"})
    world.server.add_folder(w.ADA, "Projects/2026")
    r = app.post("/api/folders/delete", {"account": w.ADA, "folder": "Projects", "confirm": True})
    assert (r.status, r.json) == (400, problems.refused(FOLDER_WORDS["has folders"]))
    assert "Projects" in on_server(world, w.ADA)
