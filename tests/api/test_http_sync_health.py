"""An account whose sync fails, over HTTP, as the status pill and Settings read it (the digest's `status`, and each
account's `live` and record in /api/account-health). Trouble that may pass, a connection lost or a server that can't take
a login for now, as a sleep or a change of network leaves for a moment, shows only once the next sync of the account has
failed too, or three minutes on, and its record keeps no error until then; meanwhile its status says it is trying again,
so nothing says it synced. A refusal shows at once (test_http_login_hold.py). A sync that works ends the run.
"""
import json
from datetime import timedelta

import pytest
import trustme

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
    assert status(app) == {**synced, "trying_again": True}
    once = health(app)
    assert (once["live"], once["healthy"], once["last_error"], once["last_error_kind"]) == ("ok", True, None, None)
    assert status(app, w.BEA)["health"] == "ok" and "trying_again" not in status(app, w.BEA)

    sync(app)
    assert status(app)["health"] == "error" and "trying_again" not in status(app)
    twice = health(app)
    assert (twice["live"], twice["healthy"]) == ("error", False) and twice["last_error"]

    sync(app)
    assert status(app)["health"] == "ok" and "trying_again" not in status(app)
    again = health(app)
    assert (again["live"], again["healthy"], again["last_error"]) == ("ok", True, None)


@TROUBLE
def test_trouble_shows_three_minutes_on_though_no_sync_has_run_since(app, world, trouble):
    sync(app)
    trouble(world.server, 1)
    sync(app)
    app.clock.shift(timedelta(seconds=THREE_MINUTES - 1))
    assert (status(app)["health"], status(app)["trying_again"]) == ("ok", True)
    app.clock.shift(timedelta(seconds=1))
    shown = status(app)
    assert shown["health"] == "error" and shown["msg"] and "trying_again" not in shown
    assert health(app)["live"] == "error"
    # A sync that works ends the run.
    sync(app)
    assert status(app)["health"] == "ok" and "trying_again" not in status(app)
    assert health(app)["live"] == "ok"


def test_the_first_sync_that_fails_shows_nothing_until_it_shows(app, world):
    """With no sync before it, the account says no more of how it stands than before any sync, but that it is trying
    again; the next sync that works says it is synced."""
    world.server.drop("LOGIN", user=w.ADA)
    sync(app)
    assert status(app) == {"health": "", "msg": "", "trying_again": True}
    assert health(app)["live"] == ""
    assert status(app, w.BEA)["health"] == "ok"
    sync(app)
    assert status(app)["health"] == "ok" and "trying_again" not in status(app)


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


@pytest.fixture
def roots_of_its_own(tmp_path, monkeypatch):
    """The server trusts only a root of the test's own: SSL_CERT_FILE, which OpenSSL and the Rust server's native roots
    read in place of the system's. The fake server's certificate doesn't chain to it. Named before `app`, it is in the
    environment the server starts with."""
    path = tmp_path / "roots.pem"
    trustme.CA().cert_pem.write_to_path(str(path))
    monkeypatch.setenv("SSL_CERT_FILE", str(path))


def test_a_certificate_that_isnt_trusted_shows_at_once(roots_of_its_own, app, world):
    """A server off this machine whose certificate no trusted root vouches for is no trouble that may pass: the password
    was never sent, and the next sync can only meet it again. Ada's server is named by an address of this machine the
    app doesn't count as one (IPv4-mapped, written in hex), so its certificate is verified rather than pinned."""
    with app.stopped():
        path = world.dir / "mailmind.config.json"
        written = json.loads(path.read_text())
        next(a for a in written["accounts"] if a["email"] == w.ADA)["host"] = "::ffff:7f00:1"
        path.write_text(json.dumps(written))
    sync(app)
    assert status(app)["health"] == "error"
    shown = health(app)
    assert (shown["live"], shown["healthy"]) == ("error", False) and shown["last_error"]
    assert status(app, w.BEA)["health"] == "ok"
