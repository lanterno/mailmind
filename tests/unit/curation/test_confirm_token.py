"""A cleanup's confirm token: proof the person saw this preview, of this way out, in the last ten minutes."""
import pytest

from mailmind.curation.domain import unsubscribe
from mailmind.platform.perimeter import tokens

ONE_CLICK = {"method": "one-click", "https_uri": "https://loom.example.test/leave?u=1"}
MAILTO = {"method": "mailto", "mailto_to": "leave@loom.example.test"}
LOOM = "news@loom.example.test"
NOW = 1_800_000_000


def token(info=ONE_CLICK, addr=LOOM, secret="s3cret", now=NOW):
    return tokens.mint(secret, unsubscribe.TOKEN_PURPOSE, unsubscribe.preview_said(addr, info), now)


def names(token, addr=LOOM, info=ONE_CLICK, secret="s3cret"):
    return tokens.names(secret, token, unsubscribe.TOKEN_PURPOSE, unsubscribe.preview_said(addr, info))


def test_it_says_when_the_preview_was_seen():
    assert token().startswith(f"{NOW}.")


@pytest.mark.parametrize("age, good", [(0, True), (unsubscribe.TOKEN_LIFE, True), (unsubscribe.TOKEN_LIFE + 1, False),
                                       (-1, False)])
def test_it_stands_for_ten_minutes(age, good):
    assert tokens.in_date(token(), NOW + age, unsubscribe.TOKEN_LIFE) is good


@pytest.mark.parametrize("bad", ["", ".", "x", ".abc", "12x.abc", "-5.abc", "٣٣.abc", "9" * 5000 + ".abc"])
def test_what_isnt_a_token_is_out_of_date_and_raises_nothing(bad):
    assert not tokens.in_date(bad, NOW, unsubscribe.TOKEN_LIFE)


def test_it_names_the_plan_it_was_given_for():
    t = token()
    assert names(t)
    assert names(t, LOOM.upper())


@pytest.mark.parametrize("secret, addr, info", [
    ("other", LOOM, ONE_CLICK),
    ("s3cret", "deals@spindle.example.test", ONE_CLICK),
    ("s3cret", LOOM, MAILTO),
    ("s3cret", LOOM, {**ONE_CLICK, "https_uri": "https://elsewhere.example.test/leave"}),
    ("s3cret", LOOM, {**MAILTO, "method": "one-click"}),
])
def test_it_names_no_other(secret, addr, info):
    assert not names(token(), addr, info, secret)


def test_its_time_cant_be_moved_on():
    mac = token().partition(".")[2]
    assert not names(f"{NOW + 500}.{mac}")


def test_a_mac_outside_ascii_is_a_no_and_not_a_crash():
    assert not names(f"{NOW}.é")


def test_one_purposes_token_stands_for_no_other():
    said = unsubscribe.preview_said(LOOM, ONE_CLICK)
    assert not tokens.names("s3cret", tokens.mint("s3cret", "another", said, NOW), unsubscribe.TOKEN_PURPOSE, said)
