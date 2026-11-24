"""Each account's colour, over HTTP: the digest names its place among the palette's, kept with the account, so the page and
the Mac colour it the same, and removing an account moves no other's."""
from fixtures import world as w

DEE = "dee@example.test"            # not one of the world's accounts


def colours(app):
    return app.get("/api/digest").json["colours"]


def test_removing_an_account_moves_no_other_accounts_colour_and_one_added_takes_the_place_it_left(app, world,
                                                                                                fake_keychain):
    assert colours(app) == {w.ADA: 0, w.BEA: 1, w.CY: 2}
    assert app.post("/api/accounts/delete", {"email": w.BEA}).status == 200
    assert colours(app) == {w.ADA: 0, w.CY: 2}
    entry = world.add_account(DEE, password="dee-password")
    fake_keychain.delete(f"account:{DEE}")
    at = {k: entry[k] for k in ("host", "port", "starttls", "smtp_host", "smtp_port", "smtp_starttls")}
    added = app.post("/api/accounts/add", {"email": DEE, "type": "imap", **at,
                                           "credential": {"style": "keychain", "value": "dee-password"}})
    assert added.status == 200, added.json
    assert colours(app) == {w.ADA: 0, w.CY: 2, DEE: 1}
