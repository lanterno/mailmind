"""Changes made while an account's server was away, over HTTP: each waits out a backoff (20 seconds, then 40, and on), and
goes as soon as the server answers again, a sync of it working after one failed, or a person says Try again
(`/api/sync-retry-account`), not when its wait runs out, minutes on. Only that account's changes go, and their tries stand.
"""
from fixtures import world as w
from mailmind.bootstrap.mailbox import store

ADAS = "ada-inbox-05-receipt.eml"
BEAS = "bea-inbox-01-client.eml"


def sync(app):
    """A sync of every account, as the page's refresh sets one going, waited out."""
    assert app.post("/api/refresh", {}).json["ok"]
    app.settle()


def where(world, name):
    [(_, box, _, _)] = world.where(name)["server"]
    return box


def queued(account):
    """The account's changes still waiting, and how many tries each has had."""
    c = store.conn()
    try:
        return [r["attempts"] for r in c.execute(
            "SELECT attempts FROM mail_commands WHERE account=? AND state='queued' ORDER BY id", (account,))]
    finally:
        c.close()


def archived_while_away(app, world, name, account):
    """`name` archived while the account's server drops the move, the click's try and the worker's that takes it over:
    it waits to try again, its wait not yet out. How many tries it has had."""
    world.server.drop("UID MOVE", user=account, times=2)
    assert app.post("/api/archive", world.copy(name)).status == 200
    app.work("commands")
    app.work("commands")
    [tries] = queued(account)
    assert where(world, name) == "INBOX" and tries >= 1
    return tries


def test_a_change_made_while_the_server_was_away_goes_once_a_sync_of_it_works(app, world):
    sync(app)
    archived_while_away(app, world, ADAS, w.ADA)
    world.server.drop("LOGIN", user=w.ADA)
    sync(app)                                      # still away: the change waits on
    app.work("commands")
    assert where(world, ADAS) == "INBOX"
    sync(app)                                      # the server answers again
    app.work("commands")
    assert (where(world, ADAS), queued(w.ADA)) == ("Archive", [])


def test_a_sync_that_works_as_ever_lets_nothing_go_early(app, world):
    sync(app)
    tries = archived_while_away(app, world, ADAS, w.ADA)
    sync(app)                                      # no sync of the account failed: its server never went away
    app.work("commands")
    assert (where(world, ADAS), queued(w.ADA)) == ("INBOX", [tries])


def test_try_again_sends_what_waited_for_that_account_alone(app, world):
    archived_while_away(app, world, ADAS, w.ADA)
    tries = archived_while_away(app, world, BEAS, w.BEA)
    assert app.post("/api/sync-retry-account", {"account": w.ADA}).json["ok"]
    app.settle()
    app.work("commands")
    assert (where(world, ADAS), queued(w.ADA)) == ("Archive", [])
    assert (where(world, BEAS), queued(w.BEA)) == ("INBOX", [tries])     # its wait, and its try, as they were

