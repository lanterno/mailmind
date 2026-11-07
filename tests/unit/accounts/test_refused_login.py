"""A login its server refused (CredentialsRejected, accounts.logins): nothing the app does sends it again until its hold
ends, the account changes or someone asks."""
from datetime import timedelta

import pytest

from fixtures import world as w
from mailmind.accounts.domain.events import CredentialsRejected
from mailmind.accounts.domain.login_hold import FIRST, LONGEST
from mailmind.bootstrap import accounts, mailbox
from mailmind.platform import bus

REFUSED = "NO [AUTHENTICATIONFAILED] invalid credentials"


def logins(world, who=w.ADA):
    return len(world.server.commands_for(who, "LOGIN"))


def sync(who=w.ADA):
    mailbox.syncing.sync_accounts({who})


def health(app, who=w.ADA):
    return next(a for a in app.get("/api/account-health").json["accounts"] if a["account"] == who)


def past_the_hold(app, seconds=FIRST):
    app.clock.shift(timedelta(seconds=seconds + 1))


@pytest.fixture(params=[w.ADA, w.CY])
def who(request):
    return request.param


def test_whoever_asks_a_refused_login_goes_once(app, world, who):
    world.server.fail("LOGIN", REFUSED, user=who, times=None)
    acct, before = accounts.get(who), logins(world, who)
    sync(who)
    assert logins(world, who) == before + 1
    sync(who)
    mailbox.syncing.backfill_round()
    mailbox.commands.drain()
    with pytest.raises(accounts.LoginUnreachable):
        mailbox.open_session(acct)
    with pytest.raises(accounts.LoginUnreachable):
        mailbox.realtime._connect(acct)
    assert logins(world, who) == before + 1


def test_while_a_login_is_held_back_the_account_reads_as_it_did_when_refused(app, world):
    world.server.fail("LOGIN", REFUSED, user=w.ADA, times=None)
    sync()
    refused = health(app)
    sync()
    held = health(app)
    assert (refused["last_error"], refused["last_error_kind"]) == (held["last_error"], held["last_error_kind"]) == ("LoginUnreachable", "AUTH")
    assert app.post("/api/accounts/test", {"email": w.ADA}).json["error"] == "AUTHENTICATIONFAILED"


def test_a_move_queues_and_waits_out_the_hold(app, world):
    world.server.fail("LOGIN", REFUSED, user=w.ADA)
    sync()
    name = "ada-inbox-05-receipt.eml"
    r = app.post("/api/archive", {"pk": world.pk(name), "mutation_id": "m-1"}).json
    assert r["status"] == "queued"
    before = logins(world)
    mailbox.commands.drain()
    assert logins(world) == before and world.server.find(w.ADA, "INBOX", w.message_id(name))
    past_the_hold(app)
    mailbox.commands.drain()
    assert logins(world) == before + 1 and not world.server.find(w.ADA, "INBOX", w.message_id(name))


def test_each_try_refused_after_a_hold_ended_waits_twice_as_long_up_to_an_hour(app, world):
    world.server.fail("LOGIN", REFUSED, user=w.ADA, times=None)
    sync()
    tries = logins(world)
    for rest in (FIRST, FIRST * 2, FIRST * 4, FIRST * 8, LONGEST, LONGEST):
        app.clock.shift(timedelta(seconds=rest - 1))
        sync()
        assert logins(world) == tries, rest
        app.clock.shift(timedelta(seconds=2))
        sync()
        assert logins(world) == tries + 1, rest
        tries += 1


def test_a_login_that_works_starts_the_count_again(app, world):
    world.server.fail("LOGIN", REFUSED, user=w.ADA, times=2)
    sync()
    past_the_hold(app)
    sync()                                                # refused again: the next waits twice as long
    past_the_hold(app, FIRST * 2)
    sync()
    assert health(app)["healthy"]
    world.server.fail("LOGIN", REFUSED, user=w.ADA)
    tries = logins(world)
    sync()
    past_the_hold(app)
    sync()
    assert logins(world) == tries + 2 and health(app)["healthy"]


@pytest.mark.parametrize("trouble", [lambda server, who: server.drop("LOGIN", user=who, times=2),
                                     lambda server, who: server.fail("LOGIN", "NO [UNAVAILABLE] busy", user=who, times=2)],
                         ids=["a lost connection", "a server down for now"])
def test_what_is_no_refusal_holds_nothing(app, world, who, trouble):
    trouble(world.server, who)
    before = logins(world, who)
    sync(who)
    sync(who)
    assert logins(world, who) == before + 2
    sync(who)
    assert health(app, who)["healthy"]


def test_a_bad_answer_to_login_is_a_refusal_too(app, world):
    world.server.fail("LOGIN", "BAD [CLIENTBUG] no", user=w.ADA, times=None)
    before = logins(world)
    sync()
    sync()
    assert logins(world) == before + 1


def test_another_account_goes_on(app, world):
    world.server.fail("LOGIN", REFUSED, user=w.ADA, times=None)
    sync()
    before = logins(world, w.BEA)
    sync(w.BEA)
    assert logins(world, w.BEA) == before + 1 and health(app, w.BEA)["healthy"]


# -- what ends a hold --------------------------------------------------------------------------------------------

def test_a_new_password_goes_at_once(app, world, who):
    world.server.accounts[who].password = "the-new-password"
    sync(who)
    assert health(app, who)["last_error_kind"] == "AUTH"
    before = logins(world, who)
    assert app.post("/api/accounts/edit", {"email": who, "credential": {"style": "plain", "value": "the-new-password"}}).json["ok"]
    sync(who)
    assert logins(world, who) == before + 1 and health(app, who)["healthy"]


def test_a_password_changed_in_the_file_by_hand_goes_at_once(app, world):
    from mailmind.bootstrap.accounts import config
    world.server.accounts[w.ADA].password = "the-new-password"
    sync()
    raw = config._raw()
    next(a for a in raw["accounts"] if a["email"] == w.ADA)["credential"] = {"plain": "the-new-password"}
    config.save(raw)
    sync()
    assert health(app)["healthy"]


def test_any_edit_of_the_account_lets_the_next_try_go(app, world):
    world.server.fail("LOGIN", REFUSED, user=w.ADA)
    sync()
    assert app.post("/api/accounts/edit", {"email": w.ADA, "alias": "Ada"}).json["ok"]
    sync()
    assert health(app)["healthy"]


def test_retry_lets_the_next_try_go(app, world, who):
    world.server.fail("LOGIN", REFUSED, user=who)
    sync(who)
    assert not health(app, who)["healthy"]
    assert app.post("/api/sync-retry-account", {"account": who}).json["ok"]
    app.settle()
    assert health(app, who)["healthy"]


def test_trusting_a_certificate_again_lets_the_next_try_go(app, world):
    world.server.fail("LOGIN", REFUSED, user=w.ADA)
    sync()
    assert app.post("/api/accounts/trust-cert", {"email": w.ADA}).json["ok"]
    app.settle()
    assert health(app)["healthy"]


def test_a_removed_account_added_back_starts_clean(app, world):
    from mailmind.bootstrap.accounts import config
    acct = accounts.get(w.ADA)
    world.server.fail("LOGIN", REFUSED, user=w.ADA)
    sync()
    assert accounts.logins.held(acct)
    assert app.post("/api/accounts/delete", {"email": w.ADA}).json["ok"]
    assert not accounts.logins.held(acct)
    raw = config._raw()
    raw["accounts"].append({"email": w.ADA, "type": acct["kind"], "host": acct["host"], "port": acct["port"],
                            "starttls": acct["starttls"], "credential": acct["credential"]})
    config.save(raw)
    assert not accounts.logins.held(acct)


# -- a person's test ---------------------------------------------------------------------------------------------

def test_testing_a_saved_account_goes_whatever_the_hold_says_and_a_login_that_works_ends_it(app, world, who):
    world.server.fail("LOGIN", REFUSED, user=who)
    sync(who)
    before = logins(world, who)
    assert accounts.logins.held(accounts.get(who))
    assert app.post("/api/accounts/test", {"email": who}).json["connect"] is True
    assert logins(world, who) == before + 1
    assert not accounts.logins.held(accounts.get(who))


def test_testing_a_saved_account_its_server_refuses_says_so_and_doesnt_lengthen_the_hold(app, world):
    world.server.fail("LOGIN", REFUSED, user=w.ADA, times=None)
    sync()
    before = logins(world)
    for _ in range(3):
        assert app.post("/api/accounts/test", {"email": w.ADA}).json == {
            "ok": True, "connect": False, "email": w.ADA, "error": "AUTHENTICATIONFAILED"}
    assert logins(world) == before + 3
    past_the_hold(app)
    sync()
    assert logins(world) == before + 4


def test_a_refused_draft_holds_nothing_of_the_account_saved_under_its_address(app, world):
    acct = accounts.get(w.ADA)
    draft = {"email": w.ADA, "type": "imap", "host": acct["host"], "port": acct["port"], "starttls": True,
             "credential": {"style": "plain", "value": "wrong"}}
    assert app.post("/api/accounts/test", {"draft": draft}).json["connect"] is False
    assert not accounts.logins.held(acct)
    sync()
    assert health(app)["healthy"]


def test_a_draft_that_logs_in_says_nothing_of_the_login_saved_under_its_address(app, world):
    acct = accounts.get(w.ADA)
    world.server.fail("LOGIN", REFUSED, user=w.ADA)
    sync()
    draft = {"email": w.ADA, "type": "imap", "host": acct["host"], "port": acct["port"], "starttls": True,
             "credential": {"style": "plain", "value": "app-password"}}
    assert app.post("/api/accounts/test", {"draft": draft}).json["connect"] is True
    assert accounts.logins.held(acct)


# -- the event ---------------------------------------------------------------------------------------------------

def test_each_refusal_is_told_once_and_a_try_held_back_isnt(app, world, who):
    heard = []
    end = bus.subscribe(CredentialsRejected, heard.append)
    try:
        world.server.fail("LOGIN", REFUSED, user=who, times=None)
        sync(who)
        sync(who)
        assert [e.email for e in heard] == [who]
        past_the_hold(app)
        sync(who)
        assert [e.email for e in heard] == [who, who]
    finally:
        end()
    assert world.server.accounts[who].password not in repr(heard)


def test_what_is_no_refusal_isnt_told(app, world):
    heard = []
    end = bus.subscribe(CredentialsRejected, heard.append)
    try:
        world.server.drop("LOGIN", user=w.ADA)
        sync()
        world.server.fail("LOGIN", "NO [UNAVAILABLE] busy", user=w.ADA)
        sync()
    finally:
        end()
    assert heard == []


def test_a_process_not_wired_holds_nothing(world):
    """The command line is one run: it says what the server said and ends, so there's nothing to hold back."""
    world.server.fail("LOGIN", REFUSED, user=w.ADA, times=2)
    before = logins(world)
    for _ in range(2):
        with pytest.raises(accounts.LoginUnreachable):
            mailbox.open_session(accounts.get(w.ADA))
    assert logins(world) == before + 2
