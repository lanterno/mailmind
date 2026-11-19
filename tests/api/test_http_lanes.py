"""The lanes, over HTTP: Feed (`is:newsletter`) and Paper trail (`is:receipt`) are the views the page and the Mac show as
lanes, and ask the search for with `lane`. A lane is the mail kept, as a folder's list is: what was deleted or is junk is
left out, wherever it is stored, and so is what a queued change is taking away. A search of the same words, with no lane,
still finds a message where it went."""
from urllib.parse import quote

import pytest

from fixtures import world as w

RECEIPT = "ada-inbox-05-receipt.eml"
PROMO = "ada-trash-01-promo.eml"        # a newsletter in the Trash as the world begins


def found(app, q, lane=None, account=w.ADA):
    """(folder, uid) of each copy `q` finds of `account`'s mail, as a lane asks for it or as a search does."""
    asked = f"/api/search?scope=account&account={account}&q={quote(q)}" + (f"&lane={lane}" if lane else "")
    return [(r["folder"], r["uid"]) for r in app.get(asked).json["items"]]


def keys(app, q, lane=None):
    asked = f"/api/search?scope=all&q={quote(q)}" + (f"&lane={lane}" if lane else "")
    return {r["key"] for r in app.get(asked).json["items"]}


def test_the_feed_leaves_out_a_newsletter_in_the_trash_which_a_search_still_finds(app, world):
    trashed = world.key(PROMO)
    assert trashed in keys(app, "is:newsletter")
    assert trashed not in keys(app, "is:newsletter", lane="feed")


@pytest.mark.parametrize("verb, folder", [("trash", "Trash"), ("junk", "Junk")])
def test_a_receipt_deleted_or_marked_junk_leaves_the_paper_trail(app, world, verb, folder):
    [(_, box, uid, _)] = world.where(RECEIPT)["server"]
    assert found(app, "is:receipt", lane="papertrail") == [(box, uid)]
    assert app.post(f"/api/{verb}", world.copy(RECEIPT)).status == 200
    [(_, box, uid, _)] = world.where(RECEIPT)["server"]
    assert box == folder
    assert found(app, "is:receipt", lane="papertrail") == []
    # The search finds it where it went, and the folder's next sync brings it no closer to the lane.
    assert found(app, "is:receipt") == [(folder, uid)]
    app.work("sync", w.ADA, folder)
    assert found(app, "is:receipt", lane="papertrail") == []
    # Undone, it is back in the Inbox, and in the lane.
    assert app.post("/api/undo", {}).json["ok"]
    [(_, box, uid, _)] = world.where(RECEIPT)["server"]
    assert found(app, "is:receipt", lane="papertrail") == [("INBOX", uid)]


def test_a_receipt_archived_stays_on_the_paper_trail(app, world):
    assert app.post("/api/archive", world.copy(RECEIPT)).status == 200
    [(_, box, uid, _)] = world.where(RECEIPT)["server"]
    assert found(app, "is:receipt", lane="papertrail") == [(box, uid)]


def test_a_receipt_a_queued_move_is_taking_away_is_on_no_lane(app, world):
    world.server.add_folder(w.ADA, "Clients")
    world.queued_move(RECEIPT, "Clients")
    assert found(app, "is:receipt", lane="papertrail") == []
    app.work("commands")
    [(_, box, uid, _)] = world.where(RECEIPT)["server"]
    assert found(app, "is:receipt", lane="papertrail") == [("Clients", uid)]
