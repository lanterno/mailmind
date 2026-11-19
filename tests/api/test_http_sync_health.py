"""An account whose sync fails, over HTTP, as the status pill and Settings read it (the digest's `status`, and each
account's `live` and record in /api/account-health). Trouble that may pass, a connection lost or a server that can't take
a login for now, as a sleep or a change of network leaves for a moment, shows only once the next sync of the account has
failed too, or three minutes on, and its record keeps no error until then; meanwhile its status says it is trying again,
so nothing says it synced. A refusal shows at once (test_http_login_hold.py). A sync that works ends the run.

The open tabs hear of each sync that fails (`sync-trouble`), so they read the status again. A connection outside a sync
that can't be made (a person's change, IDLE's, a job's) by trouble that may pass shows at once as trying again, rather
than at the next sync, minutes on; the next sync that works ends it.
"""
import json
from datetime import timedelta

import pytest
import trustme

from event_stream import Stream
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


def test_an_account_whose_password_the_keychain_lost_is_told_so_at_once(app, world, fake_keychain):
    """No password to send: no login is tried, the sync fails at once as a kind of its own, which the account's card says
    and offers Change Password for, and a Test says MailMind has none, never that a server refused it."""
    with app.stopped():
        fake_keychain.delete(f"account:{w.ADA}")
    sync(app)
    lost = health(app)
    assert (lost["healthy"], lost["last_error"], lost["last_error_kind"]) == (False, "NoCredential", "NO_PASSWORD")
    assert status(app)["health"] == "error"
    assert status(app, w.BEA)["health"] == "ok"
    tested = app.post("/api/accounts/test", {"email": w.ADA}).json
    assert (tested["connect"], tested["error"]) == (False, "no password")
    assert "this Mac's Keychain doesn't hold one" in tested["says"]


ADAS = "ada-inbox-05-receipt.eml"


def troubled(stream, who=w.ADA):
    """The frames the stream sends until it says `who`'s server didn't answer, that one last: other accounts' syncs may
    say they ended first."""
    said = f'event: sync-trouble\ndata: {json.dumps({"account": who})}'
    frames = []
    while not frames or frames[-1] != said:
        assert len(frames) < 20, frames
        frames.append("\n".join(stream.event().split("\n")[1:]))
    return frames


@TROUBLE
def test_a_sync_that_fails_tells_the_open_tabs_whether_it_shows_yet_or_not(app, world, trouble):
    s = Stream(app)
    s.frame()
    trouble(world.server, 2)
    sync(app)
    assert status(app)["trying_again"]
    troubled(s)
    sync(app)
    assert status(app)["health"] == "error"
    troubled(s)
    s.close()


def test_a_connection_that_fails_outside_a_sync_shows_at_once_and_a_sync_that_works_ends_it(app, world):
    sync(app)
    synced = status(app)
    s = Stream(app)
    s.frame()
    # A person files a message while Ada's server is away: the click's own connection can't be made.
    world.server.drop("LOGIN", user=w.ADA)
    assert app.post("/api/archive", {**world.copy(ADAS), "mutation_id": "m-away"}).json["status"] == "queued"
    assert status(app) == {**synced, "trying_again": True}
    assert status(app, w.BEA)["health"] == "ok" and "trying_again" not in status(app, w.BEA)
    troubled(s)                                     # the tabs read the status again
    # No sync has failed: none is kept as one.
    assert (health(app)["live"], health(app)["last_error"]) == ("ok", None)
    # The server answers the sync that comes next: it says so, and the change made while it was away goes now.
    sync(app)
    assert status(app)["health"] == "ok" and "trying_again" not in status(app)
    app.work("commands")
    [(_, box, _, _)] = world.where(ADAS)["server"]
    assert box == "Archive"
    s.close()


def test_a_connection_that_fails_outside_a_sync_shows_three_minutes_on_as_a_sync_would(app, world):
    sync(app)
    world.server.drop("LOGIN", user=w.ADA)
    assert app.post("/api/archive", {**world.copy(ADAS), "mutation_id": "m-away"}).json["status"] == "queued"
    app.clock.shift(timedelta(seconds=THREE_MINUTES - 1))
    assert status(app)["trying_again"]
    app.clock.shift(timedelta(seconds=1))
    assert status(app)["health"] == "error" and "trying_again" not in status(app)


def test_a_refused_login_outside_a_sync_is_left_to_the_sync_to_say(app, world):
    """A password refused shows as one at once, with its own words, by the hold every login keeps
    (test_http_login_hold.py): it is no trouble that may pass, and nothing says it is trying again."""
    sync(app)
    synced = status(app)
    world.server.fail("LOGIN", "NO [AUTHENTICATIONFAILED] invalid credentials", user=w.ADA)
    assert app.post("/api/archive", {**world.copy(ADAS), "mutation_id": "m-refused"}).json["status"] == "queued"
    assert status(app) == synced


def until_said(stream, said):
    """The frames the stream sends until one begins with `said`, that one last, each without its id."""
    frames = []
    while not frames or not frames[-1].startswith(said):
        assert len(frames) < 20, frames
        frames.append("\n".join(stream.event().split("\n")[1:]))
    return frames


@TROUBLE
def test_a_try_ends_trouble_that_may_pass_once_the_server_answers_with_no_sync_of_the_rounds(app, world, trouble):
    """While trouble may pass, the background tries the account's server every few seconds (`retry`): once it answers,
    the account says it synced and the tabs hear, within moments rather than at the next round, minutes on."""
    sync(app)
    s = Stream(app)
    s.frame()
    trouble(world.server, 1)
    sync(app)
    assert status(app)["trying_again"]
    troubled(s)
    app.work("retry")
    assert status(app)["health"] == "ok" and "trying_again" not in status(app)
    assert health(app)["live"] == "ok"
    until_said(s, f'event: sync-done\ndata: {{"account": "{w.ADA}"')
    s.close()


@TROUBLE
def test_a_try_that_fails_changes_nothing_says_nothing_and_counts_no_failed_sync(app, world, trouble):
    sync(app)
    trouble(world.server, 3)
    sync(app)
    tried = status(app)
    assert tried["trying_again"]
    s = Stream(app)
    s.frame()
    app.work("retry")
    # The server is still away: the account shows as it did, nothing is kept as a failure, and no tab hears of one.
    assert status(app) == tried
    assert (health(app)["live"], health(app)["last_error"]) == ("ok", None)
    assert app.post("/api/sync-retry-account", {"account": w.BEA}).json["ok"]
    said = until_said(s, f'event: sync-done\ndata: {{"account": "{w.BEA}"')
    assert not [f for f in said if f.startswith("event: sync-trouble")], said
    # The round's next sync is the second that fails in a row, which shows, as it would have with no try between.
    sync(app)
    assert status(app)["health"] == "error"
    s.close()


def test_a_try_asks_nothing_of_a_server_that_answers_or_turned_the_password_down(app, world):
    sync(app)
    world.server.fail("LOGIN", "NO [AUTHENTICATIONFAILED] invalid credentials", user=w.ADA)
    sync(app)
    assert status(app)["health"] == "error"
    asked = len(world.server.commands_for())
    app.work("retry")
    assert len(world.server.commands_for()) == asked
    assert status(app)["health"] == "error"
