"""Mail MailMind filed is found where it went at once, over HTTP: a message archived, marked junk, trashed or moved keeps
its row, under the folder and UID the server gave its copy there, so search finds it before that folder is synced or
opened again, and the folder's next sync stores no second row of it. Gmail's archive leaves the message in All Mail,
where it is found the same way, and an Undo takes the row back to the Inbox with the message. Filed the old way, its
row went until that folder synced, and search found nothing."""
from urllib.parse import quote

import pytest

from fixtures import world as w

RECEIPT = "ada-inbox-05-receipt.eml"
PARCEL = "cy-inbox-03-unrelated.eml"
CLIENTS = "Clients"
ALL_MAIL = "[Gmail]/All Mail"


def hits(app, account, words):
    """(folder, uid) of each copy a search of `account` finds."""
    found = app.get(f"/api/search?scope=all&account={account}&q={quote(words)}").json["items"]
    return [(r["folder"], r["uid"]) for r in found]


def stored(world, name):
    return [(r["folder"], r["uid"]) for r in world.where(name)["db"]]


@pytest.mark.parametrize("verb, folder", [("archive", "Archive"), ("junk", "Junk"), ("trash", "Trash"),
                                          ("move", CLIENTS)])
def test_mail_filed_is_found_at_once_under_the_folder_and_uid_it_has_there(app, world, verb, folder):
    body = world.copy(RECEIPT)
    if verb == "move":
        world.server.add_folder(w.ADA, CLIENTS)          # a folder never synced
        body = {**body, "to": CLIENTS}
    assert app.post(f"/api/{verb}", body).status == 200
    [(_, box, uid, _)] = world.where(RECEIPT)["server"]
    assert box == folder
    assert stored(world, RECEIPT) == [(folder, uid)]
    assert hits(app, w.ADA, "receipt 1042") == [(folder, uid)]
    # The folder's next sync finds the copy already stored, and keeps one row of it.
    app.work("sync", w.ADA, folder)
    assert stored(world, RECEIPT) == [(folder, uid)]
    assert hits(app, w.ADA, "receipt 1042") == [(folder, uid)]


def test_gmails_archive_is_found_at_once_in_all_mail(app, world):
    assert app.post("/api/archive", world.copy(PARCEL)).status == 200
    [(_, box, uid, _)] = world.where(PARCEL)["server"]
    assert box == ALL_MAIL
    assert stored(world, PARCEL) == [(ALL_MAIL, uid)]
    assert hits(app, w.CY, "parcel delivery") == [(ALL_MAIL, uid)]


def test_a_move_the_worker_applies_is_found_under_its_folder_too(app, world):
    world.server.add_folder(w.ADA, CLIENTS)
    world.queued_move(RECEIPT, CLIENTS)
    assert hits(app, w.ADA, "receipt 1042") == []           # on its way: listed nowhere yet
    app.work("commands")
    [(_, box, uid, _)] = world.where(RECEIPT)["server"]
    assert (box, hits(app, w.ADA, "receipt 1042")) == (CLIENTS, [(CLIENTS, uid)])


def test_a_filing_undone_is_back_in_the_inbox_and_gone_from_where_it_went(app, world):
    assert app.post("/api/archive", world.copy(RECEIPT)).status == 200
    assert app.post("/api/undo", {}).json["ok"]
    [(_, box, uid, _)] = world.where(RECEIPT)["server"]
    assert box == "INBOX"
    assert stored(world, RECEIPT) == [("INBOX", uid)]
    assert hits(app, w.ADA, "receipt 1042") == [("INBOX", uid)]
