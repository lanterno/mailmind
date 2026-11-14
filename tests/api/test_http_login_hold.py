"""A login its server refused, over HTTP: nothing the server does sends it again until its hold ends, the account changes,
someone asks, or the server starts again. What the page hears of it is the account's health, which reads while a login is
held as it did when it was refused, and a test of the account, which goes whatever the hold says. How long each hold lasts
is tests/unit/accounts/test_refused_login.py's, and what the Python app's insides do with one
tests/integration/accounts/test_refused_login.py's.
"""
from datetime import timedelta

import pytest

from fixtures import world as w

REFUSED = "NO [AUTHENTICATIONFAILED] invalid credentials"
FIRST = 300     # seconds: the first hold (accounts.domain.login_hold.FIRST); the suite imports nothing of the server's


def logins(world, who=w.ADA):
    return len(world.server.commands_for(who, "LOGIN"))


def sync(app):
    """A sync of every account, as the page's refresh sets one going, waited out."""
    assert app.post("/api/refresh", {}).json["ok"]
    app.settle()


def health(app, who=w.ADA):
    return next(a for a in app.get("/api/account-health").json["accounts"] if a["account"] == who)


def draft(world, password):
    return {"email": w.ADA, "type": "imap", "host": "127.0.0.1", "port": world.server.port,
            "starttls": not world.server.implicit_tls, "credential": {"style": "keychain", "value": password}}


def test_a_refused_login_waits_five_minutes_then_twice_that(app, world):
    world.server.fail("LOGIN", REFUSED, user=w.ADA, times=None)
    before = logins(world)
    sync(app)
    sync(app)
    assert logins(world) == before + 1
    app.clock.shift(timedelta(seconds=FIRST - 1))
    sync(app)
    assert logins(world) == before + 1
    app.clock.shift(timedelta(seconds=2))
    sync(app)
    assert logins(world) == before + 2
    app.clock.shift(timedelta(seconds=FIRST * 2 - 1))
    sync(app)
    assert logins(world) == before + 2
    app.clock.shift(timedelta(seconds=2))
    sync(app)
    assert logins(world) == before + 3


def test_while_a_login_is_held_back_the_account_reads_as_it_did_when_refused(app, world):
    world.server.fail("LOGIN", REFUSED, user=w.ADA, times=None)
    sync(app)
    refused = health(app)
    sync(app)
    held = health(app)
    assert not held["healthy"] and held["last_error_kind"] == "AUTH"
    assert (held["last_error"], held["last_error_kind"]) == (refused["last_error"], refused["last_error_kind"])


def test_another_account_goes_on(app, world):
    world.server.fail("LOGIN", REFUSED, user=w.ADA, times=None)
    before = logins(world, w.BEA)
    sync(app)
    sync(app)
    assert logins(world, w.BEA) == before + 2 and health(app, w.BEA)["healthy"]


def test_a_bad_answer_to_login_is_a_refusal_too(app, world):
    world.server.fail("LOGIN", "BAD [CLIENTBUG] no", user=w.ADA, times=None)
    before = logins(world)
    sync(app)
    sync(app)
    assert logins(world) == before + 1


@pytest.mark.parametrize("trouble", [lambda server: server.drop("LOGIN", user=w.ADA, times=2),
                                     lambda server: server.fail("LOGIN", "NO [UNAVAILABLE] busy", user=w.ADA, times=2)],
                         ids=["a lost connection", "a server down for now"])
def test_what_is_no_refusal_holds_nothing(app, world, trouble):
    trouble(world.server)
    before = logins(world)
    sync(app)
    sync(app)
    assert logins(world) == before + 2
    sync(app)
    assert health(app)["healthy"]


# -- what ends a hold --------------------------------------------------------------------------------------------

def test_a_new_password_goes_at_once(app, world):
    world.server.accounts[w.ADA].password = "the-new-password"
    sync(app)
    assert health(app)["last_error_kind"] == "AUTH"
    before = logins(world)
    new = {"style": "keychain", "value": "the-new-password"}
    assert app.post("/api/accounts/edit", {"email": w.ADA, "credential": new}).json["ok"]
    sync(app)
    assert logins(world) == before + 1 and health(app)["healthy"]


def test_any_edit_of_the_account_lets_the_next_try_go(app, world):
    world.server.fail("LOGIN", REFUSED, user=w.ADA)
    sync(app)
    assert app.post("/api/accounts/edit", {"email": w.ADA, "alias": "Ada"}).json["ok"]
    sync(app)
    assert health(app)["healthy"]


def test_retry_lets_the_next_try_go(app, world):
    world.server.fail("LOGIN", REFUSED, user=w.ADA)
    sync(app)
    assert not health(app)["healthy"]
    assert app.post("/api/sync-retry-account", {"account": w.ADA}).json["ok"]
    app.settle()
    assert health(app)["healthy"]


def test_trusting_a_certificate_again_lets_the_next_try_go(app, world):
    world.server.fail("LOGIN", REFUSED, user=w.ADA)
    sync(app)
    assert app.post("/api/accounts/trust-cert", {"email": w.ADA}).json["ok"]
    app.settle()
    assert health(app)["healthy"]


def test_a_start_lets_the_next_try_go(app, world):
    world.server.fail("LOGIN", REFUSED, user=w.ADA, times=None)
    before = logins(world)
    sync(app)
    sync(app)
    app.restart()
    sync(app)
    assert logins(world) == before + 2


# -- a person's test ---------------------------------------------------------------------------------------------

def test_testing_an_account_goes_whatever_the_hold_says_and_a_login_that_works_ends_it(app, world):
    world.server.fail("LOGIN", REFUSED, user=w.ADA)
    sync(app)
    before = logins(world)
    assert app.post("/api/accounts/test", {"email": w.ADA}).json["connect"] is True
    sync(app)
    assert logins(world) == before + 2 and health(app)["healthy"]


def test_testing_an_account_its_server_refuses_says_so_and_doesnt_lengthen_the_hold(app, world):
    world.server.fail("LOGIN", REFUSED, user=w.ADA, times=None)
    sync(app)
    before = logins(world)
    for _ in range(3):
        assert app.post("/api/accounts/test", {"email": w.ADA}).json == {
            "ok": True, "connect": False, "email": w.ADA, "error": "AUTHENTICATIONFAILED"}
    assert logins(world) == before + 3
    app.clock.shift(timedelta(seconds=FIRST + 1))
    sync(app)
    assert logins(world) == before + 4


def test_a_refused_draft_holds_nothing_of_the_account_saved_under_its_address(app, world):
    assert app.post("/api/accounts/test", {"draft": draft(world, "wrong")}).json["connect"] is False
    before = logins(world)
    sync(app)
    assert logins(world) == before + 1 and health(app)["healthy"]


def test_a_draft_that_logs_in_says_nothing_of_the_login_saved_under_its_address(app, world):
    world.server.fail("LOGIN", REFUSED, user=w.ADA)
    sync(app)
    assert app.post("/api/accounts/test", {"draft": draft(world, world.server.accounts[w.ADA].password)}).json["connect"] is True
    before = logins(world)
    sync(app)
    assert logins(world) == before and not health(app)["healthy"]
