"""mailmind.accounts.domain.login_hold: what counts as a refusal, which login a hold names, and how long it lasts."""
import pytest

from mailmind.accounts.domain import login_hold
from mailmind.accounts.domain.login_hold import FIRST, LONGEST, Hold, after_refusal, login_id

ADA = {"email": "ada@example.test", "host": "imap.example.test", "port": 993, "starttls": False, "strip_spaces": False,
       "credential": {"plain": "hunter2"}, "alias": "Ada", "trash": "Trash"}


@pytest.mark.parametrize("said, refusal", [
    ("b'[AUTHENTICATIONFAILED] invalid credentials'", True),
    ("LOGIN command error: BAD [b'no']", True),
    ("Invalid username or password", True),
    ("b'[UNAVAILABLE] try later'", False),
    ("b'[unavailable] try later'", False),
])
def test_a_server_saying_its_down_for_now_is_no_refusal(said, refusal):
    assert login_hold.refuses(said) is refusal


def test_a_login_is_the_same_login_whatever_else_the_account_says():
    assert login_id(ADA) == login_id({**ADA, "alias": "Ada L.", "trash": "Bin"})


@pytest.mark.parametrize("changed", [{"email": "bea@example.test"}, {"host": "other.example.test"}, {"port": 143}, {"starttls": True},
                                     {"strip_spaces": True}, {"credential": {"plain": "hunter3"}}, {"credential": {"op": "op://V/I/F"}}], ids=repr)
def test_another_server_address_or_credential_is_another_login(changed):
    assert login_id(ADA) != login_id({**ADA, **changed})


def test_a_logins_id_says_nothing_of_it():
    assert "hunter2" not in login_id(ADA) and "ada" not in login_id(ADA) and len(login_id(ADA)) == 64


def test_a_first_refusal_holds_for_the_first_rest():
    assert after_refusal(None, "L", 1000) == Hold("L", 1000 + FIRST, FIRST)


def test_a_refusal_after_a_hold_ended_doubles_it_up_to_the_longest():
    rests = []
    hold, now = None, 1000.0
    for _ in range(7):
        hold = after_refusal(hold, "L", now)
        rests.append(hold.rest)
        now = hold.until
    assert rests == [300, 600, 1200, 2400, LONGEST, LONGEST, LONGEST]


def test_two_tries_that_crossed_count_for_one():
    first = after_refusal(None, "L", 1000)
    assert after_refusal(first, "L", 1001) == Hold("L", 1001 + FIRST, FIRST)


def test_another_login_starts_again():
    long = Hold("L", 1000, LONGEST)
    assert after_refusal(long, "M", 2000).rest == FIRST


def test_a_hold_stops_only_its_login_and_only_until_it_ends():
    hold = Hold("L", 1000, FIRST)
    assert hold.stops("L", 999)
    assert not hold.stops("L", 1000) and not hold.stops("M", 999)
