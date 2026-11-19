"""A folder another client deleted or renamed, over HTTP against the fake server. It is forgotten once the server is known
not to have it: a LIST that worked (the rail's) leaves it out, or a SELECT's NO says [NONEXISTENT], or a bare NO comes
and a LIST that works leaves it out. Then its mail leaves the lists, reconcile asks for it no more, a message opened
from it says why, and a change queued for it is given back, the tray saying its folder went. A NO that says nothing of the
folder, a LIST that fails and a server away never forget one.

Ada's Archive holds one message the world synced, its body left on the server until it is opened.
"""
import urllib.parse

import problems
from fixtures import world as w

ARCHIVED = "ada-archive-01-old-thread.eml"
LUNCH = "ada-inbox-01-lunch.eml"
FOLDER_GONE = "This message’s folder isn’t on the server any more: it was deleted or renamed somewhere else."


def deleted_elsewhere(world, folder="Archive", who=w.ADA):
    """Another client deletes `who`'s `folder`, and the app hears nothing of it."""
    with world.server.lock:
        world.server.accounts[who].mailboxes.pop(folder)


def archive(args):
    """Whether a command's arguments name Ada's Archive, for a fault aimed at it alone."""
    return bool(args) and str(args[0]) == "Archive"


def examined(world, folder="Archive"):
    return [c for c in world.server.commands_for(w.ADA, "EXAMINE") if folder in c["line"]]


def on_rail(app, who=w.ADA):
    return [f["name"] for f in app.get(f"/api/folders?account={who}").json["folders"]]


def listed(app, folder, who=w.ADA):
    r = app.get("/api/messages?" + urllib.parse.urlencode({"account": who, "folder": folder}))
    return [row["subject"] for row in r.json["items"]]


def reconcile(app):
    return app.post("/api/reconcile", {"account": w.ADA}).json


def kept(world):
    """Whether the Archive's message is still stored."""
    return [row["folder"] for row in world.where(ARCHIVED)["db"]] == ["Archive"]


def test_a_folder_another_client_deleted_leaves_the_rail_and_its_mail_the_lists(app, world):
    assert kept(world)
    deleted_elsewhere(world)
    assert "Archive" not in on_rail(app)
    assert world.where(ARCHIVED)["db"] == []
    assert listed(app, "Archive") == []
    asked = len(examined(world))
    assert reconcile(app)["total_pruned"] == 0
    assert len(examined(world)) == asked, "reconcile asks for it no more"


def test_reconcile_forgets_a_folder_whose_select_says_nonexistent_without_a_list(app, world):
    deleted_elsewhere(world)
    lists = len(world.server.commands_for(w.ADA, "LIST"))
    r = reconcile(app)
    assert (r["results"][0]["error"], r["total_ghosts"], r["total_pruned"]) == (None, 1, 1)
    assert len(world.server.commands_for(w.ADA, "LIST")) == lists, "the NO said so: no LIST is asked"
    assert world.where(ARCHIVED)["db"] == []
    asked = len(examined(world))
    reconcile(app)
    assert len(examined(world)) == asked


def test_a_bare_no_for_a_folder_a_list_that_works_leaves_out_is_the_folder_gone(app, world):
    deleted_elsewhere(world)
    world.server.fail("EXAMINE", "NO can't open that", when=archive, user=w.ADA)
    assert reconcile(app)["total_pruned"] == 1
    assert world.where(ARCHIVED)["db"] == []


def test_a_dry_run_counts_a_gone_folders_mail_and_forgets_nothing(app, world):
    deleted_elsewhere(world)
    r = app.post("/api/reconcile", {"account": w.ADA, "dry_run": True}).json
    assert (r["total_ghosts"], r["total_pruned"]) == (1, 0)
    assert kept(world)


def test_a_folder_is_never_forgotten_on_a_refusal_that_says_nothing_of_it_or_a_server_away(app, world):
    world.server.fail("EXAMINE", "NO [UNAVAILABLE] not now", when=archive, user=w.ADA)
    assert reconcile(app)["results"][0]["error"]                           # the LIST still names it
    assert kept(world)
    deleted_elsewhere(world)
    world.server.fail("EXAMINE", "NO can't open that", when=archive, user=w.ADA)
    world.server.fail("LIST", "NO [UNAVAILABLE] can't list now", user=w.ADA)
    assert reconcile(app)["results"][0]["error"]                           # nothing is known of it
    assert kept(world)
    world.server.drop("EXAMINE", when=archive, user=w.ADA)
    assert reconcile(app)["results"][0]["error"]                           # the connection went
    assert kept(world)


def test_a_message_opened_from_a_folder_another_client_deleted_says_so_and_leaves_the_lists(app, world):
    copy = world.copy(ARCHIVED)
    deleted_elsewhere(world)
    r = app.get("/api/message/body?" + urllib.parse.urlencode(copy))
    assert (r.status, r.json) == (404, problems.not_found(FOLDER_GONE))
    assert world.where(ARCHIVED)["db"] == []
    assert "Archive" not in on_rail(app)
    r = app.get("/api/message/body?" + urllib.parse.urlencode(copy))
    assert (r.status, r.json) == (404, problems.not_found("not found"))


def test_a_move_queued_into_a_folder_another_client_deleted_is_given_back_saying_its_folder_went(app, world):
    world.server.drop("SELECT", user=w.ADA)
    moved = app.post("/api/move", {**world.copy(LUNCH), "to": "Archive", "mutation_id": "m-gone"}).json
    assert moved["status"] == "queued"
    assert world.where(LUNCH)["db"] == []                                  # on its way
    deleted_elsewhere(world)
    assert "Archive" not in on_rail(app)
    assert [row["folder"] for row in world.where(LUNCH)["db"]] == ["INBOX"], "listed where it was"
    tray = app.get("/api/pending").json
    assert [(item["state"], item["error"]) for item in tray["items"]] == [("error", "folder_gone")]
    assert (tray["waiting"], tray["stuck"]) == (0, 1)
    mark = world.mark()
    app.work("commands")
    assert world.writes(mark) == [], "nothing is tried"
