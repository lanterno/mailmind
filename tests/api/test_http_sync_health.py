"""An account whose sync fails, over HTTP, as the status pill and Settings read it (the digest's `status`, and each
account's `live` and record in /api/account-health). Trouble that may pass, a connection lost or a server that can't take
a login for now, as a sleep or a change of network leaves for a moment, shows only once the next sync of the account has
failed too, or three minutes on, and its record keeps no error until then. A refusal shows at once
(test_http_login_hold.py). A sync that works ends the run.
"""
from datetime import timedelta

import pytest

from fixtures import world as w

THREE_MINUTES = 180     # seconds: domain.sync_health.SHOWS_AFTER_SECONDS; the suite imports nothing of the server's

TROUBLE = pytest.mark.parametrize(
    "trouble", [lambda server, times: server.drop("LOGIN", user=w.ADA, times=times),
                lambda server, times: server.fail("LOGIN", "NO [UNAVAILABLE] busy", user=w.ADA, times=times)],
    ids=["a lost connection", "a server busy for now"])


def sync(app):
    """A sync of every account, as the page's refresh sets one going, waited out."""
    assert app.post("/api/refresh", {}).json["ok"]
    app.settle()


def status(app, who=w.ADA):
    return app.get("/api/digest").json["status"].get(who)


def health(app, who=w.ADA):
    return next(a for a in app.get("/api/account-health").json["accounts"] if a["account"] == who)


@TROUBLE
def test_trouble_once_shows_nothing_and_twice_in_a_row_shows(app, world, trouble):
    sync(app)
    synced = status(app)
    assert synced["health"] == "ok"
    trouble(world.server, 2)

    sync(app)
    assert status(app) == synced
    once = health(app)
    assert (once["live"], once["healthy"], once["last_error"], once["last_error_kind"]) == ("ok", True, None, None)
    assert status(app, w.BEA)["health"] == "ok"

    sync(app)
    assert status(app)["health"] == "error"
    twice = health(app)
    assert (twice["live"], twice["healthy"]) == ("error", False) and twice["last_error"]

    sync(app)
    assert status(app)["health"] == "ok"
    again = health(app)
    assert (again["live"], again["healthy"], again["last_error"]) == ("ok", True, None)


@TROUBLE
def test_trouble_shows_three_minutes_on_though_no_sync_has_run_since(app, world, trouble):
    sync(app)
    trouble(world.server, 1)
    sync(app)
    app.clock.shift(timedelta(seconds=THREE_MINUTES - 1))
    assert status(app)["health"] == "ok"
    app.clock.shift(timedelta(seconds=1))
    shown = status(app)
    assert shown["health"] == "error" and shown["msg"]
    assert health(app)["live"] == "error"
    # A sync that works ends the run.
    sync(app)
    assert status(app)["health"] == "ok"
    assert health(app)["live"] == "ok"


def test_the_first_sync_that_fails_shows_nothing_until_it_shows(app, world):
    """With no sync before it, the account says nothing of how it stands, as before any sync."""
    world.server.drop("LOGIN", user=w.ADA)
    sync(app)
    assert status(app) is None
    assert health(app)["live"] == ""
    assert status(app, w.BEA)["health"] == "ok"


def test_a_removed_accounts_trouble_goes_with_it(app, world):
    """Removing an account keeps nothing of how its syncs went: a run of trouble that would show by its age shows
    nothing, and the other accounts stand as they did."""
    sync(app)
    world.server.drop("LOGIN", user=w.ADA)
    sync(app)
    assert app.post("/api/accounts/delete", {"email": w.ADA}).status == 200
    assert status(app) is None
    app.clock.shift(timedelta(seconds=THREE_MINUTES))
    assert status(app) is None
    assert status(app, w.BEA)["health"] == "ok"
