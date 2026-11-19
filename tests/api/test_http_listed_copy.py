"""One message's row as its folder lists it, over HTTP (`GET /api/message/row`): by the account, the folder and its UID
there, or its key where the UID isn't known, from what is stored alone, so a shell shows a message it didn't come to from
a list (a notification, a move's Undo) without reading the folder a page at a time. A folder that lists none of it says
not found, and a request that names no account, or neither UID nor key, is refused."""
from urllib.parse import quote, urlencode

import pytest

from fixtures import world as w

RECEIPT = "ada-inbox-05-receipt.eml"
ARCHIVED = "ada-archive-01-old-thread.eml"
CLIENT = "bea-clients-01-harbor.eml"


def asked(app, **given):
    return app.get("/api/message/row?" + urlencode(given))


def as_listed(app, world, name):
    """The message's row as its folder's list gives it."""
    account, folder, _ = world.placed[name]
    page = app.get(f"/api/messages?account={account}&folder={quote(folder)}&limit=500").json
    [listed] = [r for r in page["items"] if r["key"] == world.key(name)]
    return listed


@pytest.mark.parametrize("name", [RECEIPT, ARCHIVED, CLIENT])
def test_a_messages_row_is_the_one_its_folder_lists_by_its_uid_or_its_key(app, world, name):
    account, folder, uid = world.placed[name]
    listed = as_listed(app, world, name)
    assert asked(app, account=account, folder=folder, uid=uid).json == {"ok": True, "row": listed}
    assert asked(app, account=account, folder=folder, key=world.key(name)).json["row"] == listed
    assert asked(app, account=account, folder=folder, uid=uid, key=world.key(name)).json["row"] == listed


def test_one_its_folder_doesnt_list_is_not_found(app, world):
    account, folder, uid = world.placed[RECEIPT]
    assert asked(app, account=account, folder=folder, uid=uid + 1000).status == 404
    assert asked(app, account=account, folder="Archive", uid=uid).status == 404           # another folder's UID
    assert asked(app, account=w.BEA, folder=folder, uid=uid).status == 404                # another account's
    assert asked(app, account=account, folder=folder, uid=uid, key=world.key(ARCHIVED)).status == 404
    assert app.post("/api/snooze", {**world.copy(RECEIPT), "preset": "tomorrow"}).status == 200
    assert asked(app, account=account, folder=folder, uid=uid).status == 404              # snoozed: its list hides it


def test_a_message_filed_is_found_under_the_folder_and_uid_it_has_there(app, world):
    assert app.post("/api/archive", world.copy(RECEIPT)).status == 200
    [(_, folder, uid, _)] = world.where(RECEIPT)["server"]
    assert asked(app, account=w.ADA, folder=folder, uid=uid).json["row"]["key"] == world.key(RECEIPT)


@pytest.mark.parametrize("given", [{"folder": "INBOX", "uid": 1}, {"account": w.ADA, "folder": "INBOX"},
                                   {"account": "", "folder": "INBOX", "uid": 1}])
def test_one_that_names_no_account_or_neither_uid_nor_key_is_refused(app, given):
    r = asked(app, **given)
    assert (r.status, r.json["type"]) == (400, "/problems/refused")


def test_a_uid_that_isnt_a_number_is_a_bad_request(app):
    assert asked(app, account=w.ADA, folder="INBOX", uid="seven").status == 400
