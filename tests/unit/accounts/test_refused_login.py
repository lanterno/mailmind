"""A login its server refused, held back (mailmind.accounts.application.logins.Logins): how long each hold lasts, which login it
names, and what ends it, on a clock the test moves. That nothing the app does sends a held login, and what tells Logins of each
refusal, login and change, is tests/integration/accounts/test_refused_login.py."""
from datetime import datetime, timedelta, timezone

import pytest
import time_machine

from mailmind.accounts.application.logins import Logins
from mailmind.accounts.domain.events import AccountChanged, AccountRemoved, CredentialsRejected
from mailmind.accounts.domain.login_hold import FIRST, LONGEST, login_id

ADA = {"email": "ada@example.test", "host": "imap.example.test", "port": 993, "starttls": False, "strip_spaces": False,
       "credential": {"plain": "app-password"}}
BEA = {**ADA, "email": "bea@example.test"}


@pytest.fixture
def clock():
    with time_machine.travel(datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc), tick=False) as traveller:
        yield traveller


def refused(logins, acct=ADA):
    logins.rejected(CredentialsRejected(acct["email"], login_id(acct)))


def test_a_refusal_holds_that_login_back_for_the_first_rest(clock):
    logins = Logins()
    assert not logins.held(ADA)
    refused(logins)
    clock.shift(timedelta(seconds=FIRST - 1))
    assert logins.held(ADA)
    clock.shift(timedelta(seconds=2))
    assert not logins.held(ADA)


def test_each_try_refused_after_a_hold_ended_waits_twice_as_long_up_to_an_hour(clock):
    logins = Logins()
    refused(logins)
    for rest in (FIRST, FIRST * 2, FIRST * 4, FIRST * 8, LONGEST, LONGEST):
        clock.shift(timedelta(seconds=rest - 1))
        assert logins.held(ADA), rest
        clock.shift(timedelta(seconds=2))
        assert not logins.held(ADA), rest
        refused(logins)


def test_a_login_that_works_starts_the_count_again(clock):
    logins = Logins()
    refused(logins)
    clock.shift(timedelta(seconds=FIRST + 1))
    refused(logins)                                            # refused again: the next waits twice as long
    clock.shift(timedelta(seconds=FIRST * 2 + 1))
    logins.accepted(ADA["email"])
    refused(logins)
    clock.shift(timedelta(seconds=FIRST + 1))
    assert not logins.held(ADA)


def test_another_server_address_or_credential_isnt_held(clock):
    """A password changed in the file by hand goes at once: the hold named the login that was refused."""
    logins = Logins()
    refused(logins)
    assert logins.held(ADA)
    assert not logins.held({**ADA, "credential": {"plain": "the-new-password"}})
    assert not logins.held({**ADA, "host": "imap2.example.test"})
    assert not logins.held(BEA)


@pytest.mark.parametrize("event", [AccountChanged, AccountRemoved])
def test_the_account_changed_or_removed_lets_the_next_try_go(clock, event):
    logins = Logins()
    refused(logins)
    refused(logins, BEA)
    logins.changed(event(ADA["email"]))
    assert not logins.held(ADA) and logins.held(BEA)


def test_someone_asking_lets_the_next_try_go_and_a_refusal_then_holds_for_the_first_rest(clock):
    logins = Logins()
    refused(logins)
    clock.shift(timedelta(seconds=FIRST + 1))
    refused(logins)
    logins.forget(ADA["email"])
    assert not logins.held(ADA)
    refused(logins)
    clock.shift(timedelta(seconds=FIRST + 1))
    assert not logins.held(ADA)
