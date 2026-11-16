"""Removing an account over HTTP (POST /api/accounts/delete) may let go of what this Mac keeps of its mail too
(`delete_mail`): its stored copies with their bodies, what was said of them, and each context's state under its address.
Its mail on the server is untouched, and so is every other account's here."""
from fixtures import world as w

LUNCH = "ada-inbox-01-lunch.eml"          # Ada's
CLIENT = "bea-inbox-01-client.eml"        # Bea's


def stored(account):
    """How many rows of `account`'s this Mac keeps in each table that keeps them under its address."""
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return {table: c.execute(f"SELECT count(*) FROM {table} WHERE account=?", (account,)).fetchone()[0]
                for table in ("messages", "message_identity", "sync_state")}
    finally:
        c.close()


def test_removing_an_account_and_its_mail_lets_go_of_what_this_mac_keeps_of_it(app, world):
    on_server = len(world.server.messages(w.ADA, "INBOX"))
    kept = stored(w.ADA)["messages"]
    assert kept > 0
    r = app.post("/api/accounts/delete", {"email": w.ADA, "delete_mail": True})
    assert (r.status, r.json) == (200, {"ok": True, "removed": w.ADA, "mail_deleted": kept})
    assert stored(w.ADA) == {"messages": 0, "message_identity": 0, "sync_state": 0}
    assert world.where(LUNCH)["db"] == []
    assert len(world.server.messages(w.ADA, "INBOX")) == on_server          # the server's mail is untouched
    assert [d["account"] for d in world.where(CLIENT)["db"]] == [w.BEA]      # and so is everyone else's here


def test_removing_an_account_alone_keeps_its_mail_here(app, world):
    kept = stored(w.ADA)
    r = app.post("/api/accounts/delete", {"email": w.ADA})
    assert (r.status, r.json) == (200, {"ok": True, "removed": w.ADA})
    assert stored(w.ADA) == kept


def test_an_account_no_one_saved_lets_go_of_nothing(app, world):
    kept = stored(w.ADA)
    r = app.post("/api/accounts/delete", {"email": "zed@example.test", "delete_mail": True})
    assert r.status == 404
    assert stored(w.ADA) == kept
