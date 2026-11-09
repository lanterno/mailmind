"""Where a filing lands: junk goes to the folder the server calls Junk, wherever it lists it."""
from fixtures import world as w
from world_helpers import boxes


def test_hwf_junk_goes_to_the_special_use_junk_folder(app, world):
    from fakes.imap_server import Mailbox
    a = world.server.accounts[w.ADA]
    with world.server.lock:                                      # listed first, as a server may list it
        a.mailboxes = {"Bulk orders": Mailbox("Bulk orders", uidvalidity=1_700_000_999), **a.mailboxes}
    name = "ada-inbox-12-scam.eml"
    assert app.post("/api/junk", world.copy(name)).json["ok"]
    assert boxes(world, name)[0] == [(w.ADA, "Junk")]
