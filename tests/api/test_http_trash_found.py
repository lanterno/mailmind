"""Where an account's Trash is, over HTTP: the folder the account names when its server lists it, else the one LIST marks
\\Trash, then one whose name says trash. A trash, its Undo and a sweep all go there, so iCloud's "Deleted Messages" and a
localised Gmail's Trash work with no setting. Which folder plays Trash is tests/unit/mailbox/test_find_trash.py's."""
from fixtures import world as w
from world_helpers import server_boxes

LUNCH = "ada-inbox-01-lunch.eml"
JUNK = ("ada-inbox-03-newsletter.eml", "ada-inbox-04-ci.eml", "ada-inbox-12-scam.eml")


def rename(world, who, old, new, special=None):
    """`who`'s server keeps its folder `old` as `new` from now on, with what was in it, marked `special` if given."""
    with world.server.lock:
        boxes = world.server.accounts[who].mailboxes
        box = boxes.pop(old)
        box.name = new
        if special is not None:
            box.special = special
        boxes[new] = box


def test_a_trash_goes_to_the_folder_list_marks_trash_and_its_undo_brings_it_back(app, world):
    rename(world, w.ADA, "Trash", "Deleted Messages")
    assert app.post("/api/trash", {**world.copy(LUNCH)}).json["ok"]
    assert server_boxes(world, LUNCH) == [(w.ADA, "Deleted Messages")]
    assert app.post("/api/undo", {}).json["ok"]
    assert server_boxes(world, LUNCH) == [(w.ADA, "INBOX")]


def test_with_no_folder_marked_trash_one_whose_name_says_so_is_found(app, world):
    rename(world, w.ADA, "Trash", "Deleted Messages", special="")
    assert app.post("/api/trash", {**world.copy(LUNCH)}).json["ok"]
    assert server_boxes(world, LUNCH) == [(w.ADA, "Deleted Messages")]


def test_a_localised_gmails_trash_is_found(app, world):
    rename(world, w.CY, "[Gmail]/Trash", "[Google Mail]/Papierkorb")
    name = "cy-inbox-03-unrelated.eml"
    assert app.post("/api/trash", {**world.copy(name)}).json["ok"]
    assert (w.CY, "[Google Mail]/Papierkorb") in server_boxes(world, name)
    assert app.post("/api/undo", {}).json["ok"]
    assert (w.CY, "[Google Mail]/Papierkorb") not in server_boxes(world, name)


def test_a_sweep_moves_the_junk_to_the_trash_the_server_lists(app, world):
    rename(world, w.ADA, "Trash", "Deleted Messages")
    before = len(world.server.messages(w.ADA, "Deleted Messages"))
    swept = app.post("/api/sweep", {"account": w.ADA}).json
    [ada] = swept["results"]
    assert ada["moved"] > 0 and ada["error"] is None
    assert len(world.server.messages(w.ADA, "Deleted Messages")) == before + ada["moved"]
    assert "Trash" not in world.server.accounts[w.ADA].mailboxes


def test_a_sweeps_undo_brings_its_mail_back_from_the_trash_the_server_lists(app, world):
    rename(world, w.ADA, "Trash", "Deleted Messages")
    for name in JUNK:
        world.server_flags(name, "\\Seen")
    swept = app.post("/api/sweep", {"account": w.ADA, "undo_group": "ug-sweep-found"}).json
    assert (swept["undo_group"], swept["results"][0]["moved"]) == ("ug-sweep-found", 3)
    for name in JUNK:
        assert [box for _, box, *_ in world.where(name)["server"]] == ["Deleted Messages"], name
    undone = app.post("/api/undo", {}).json
    assert (undone["ok"], undone["reversed"], undone["failed"]) == (True, 3, 0)
    for name in JUNK:
        assert [box for _, box, *_ in world.where(name)["server"]] == ["INBOX"], name
