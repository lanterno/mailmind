"""What the builder makes of recipients as they were typed: a draft keeps one that isn't an address yet, on one line in a
header of MailMind's own (To and Cc) or nowhere in the bytes (Bcc), and a send is refused at it in words that name it.
The served round trip, save to reopen to send, on both servers, is tests/api/test_http_draft_addresses.py."""
import email
import email.policy

import pytest

from mailmind.compose.domain import builder
from mailmind.kernel.addresses import MAX_RECIPIENTS

LIN = "Lin Mori <lin@example.test>"


def spec(**kw):
    return {"account": "ada@example.test", "account_name": "Ada", "to": [LIN], "cc": [], "bcc": [], "subject": "Plans",
            "text": "Hello", **kw}


def parsed(built):
    return email.message_from_bytes(built["raw"], policy=email.policy.default)


# -- on one line ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("typed, line", [
    ("bob@example", "bob@example"),
    ("  bob@example \t", "bob@example"),
    ("bob@example\r\nBcc: eve@example.test", "bob@example Bcc: eve@example.test"),
    ("a\x00b\x7fc\x85d", "a b c d"),
    ("a b c", "a b c"),
    ("a \r\n\r\n b", "a b"),
    ("\r\n", ""),
])
def test_what_was_typed_goes_on_one_line(typed, line):
    assert builder.one_line(typed) == line


def test_a_character_that_shows_as_nothing_stays_in_the_line():
    assert builder.one_line("li​n@example.test") == "li​n@example.test"


# -- the words a send is told ---------------------------------------------------------------------------------------

def test_a_refusal_names_what_was_typed_and_where():
    assert builder.not_an_address("Cc", "bob@example") == "“bob@example” in Cc isn’t a valid address."


def test_it_names_it_on_one_line():
    assert builder.not_an_address("To", "bob@example\r\nBcc: eve@example.test") == (
        "“bob@example Bcc: eve@example.test” in To isn’t a valid address.")


def test_it_cuts_a_long_one_short():
    said = builder.not_an_address("To", "x" * 200)
    assert said == f"“{'x' * (builder.SHOWN_MOST - 1)}…” in To isn’t a valid address."


@pytest.mark.parametrize("hidden", ["​", "‮", "⁦", "﻿"])
def test_it_writes_a_character_that_shows_as_nothing_as_its_escape(hidden):
    assert builder.not_an_address("To", f"li{hidden}n@example.test") == (
        f"“li\\u{ord(hidden):04x}n@example.test” in To isn’t a valid address.")


# -- recipients as typed ----------------------------------------------------------------------------------------------

def test_a_send_is_refused_at_the_first_that_isnt_an_address():
    with pytest.raises(ValueError, match="^“bob@example” in To isn’t a valid address.$"):
        builder.recipients("To", [LIN, "bob@example", "cy@"], draft=False)


def test_a_draft_keeps_each_that_isnt_after_the_addresses_once_whatever_its_case():
    pairs, typed = builder.recipients("To", ["bob@example", LIN, "BOB@EXAMPLE", "lin@example.test", " ", "cy@\n"], draft=True)
    assert (pairs, typed) == ([("Lin Mori", "lin@example.test")], ["bob@example", "cy@"])


def test_a_string_of_addresses_is_still_judged_whole():
    assert builder.recipients("To", "lin@example.test, Bo <bo@example.test>", draft=False) == (
        [("", "lin@example.test"), ("Bo", "bo@example.test")], [])


# -- what a draft's bytes carry ---------------------------------------------------------------------------------------

def test_a_draft_keeps_to_and_cc_that_arent_addresses_in_headers_of_its_own():
    msg = parsed(builder.build_message(spec(to=[LIN, "bob@example"], cc=["cy@", "Dee <dee@example.test>"]), draft=True))
    assert (str(msg["To"]), str(msg["Cc"])) == (LIN, "Dee <dee@example.test>")
    assert (msg.get_all(builder.TYPED["To"]), msg.get_all(builder.TYPED["Cc"])) == (["bob@example"], ["cy@"])


def test_a_draft_keeps_bcc_that_isnt_an_address_nowhere_in_its_bytes():
    built = builder.build_message(spec(bcc=["eve at example", "Quiet <quiet@example.test>"]), draft=True)
    assert b"eve at example" not in built["raw"] and b"quiet" not in built["raw"].lower()
    assert built["envelope"] == ["lin@example.test", "quiet@example.test"]


def test_a_line_break_typed_in_a_recipient_makes_no_header():
    msg = parsed(builder.build_message(spec(to=[LIN, "bob@example\r\nBcc: eve@example.test\r\nX-Injected: yes"]), draft=True))
    assert (msg.get_all("Bcc"), msg.get_all("X-Injected"), str(msg["To"])) == (None, None, LIN)
    assert msg.get_all(builder.TYPED["To"]) == ["bob@example Bcc: eve@example.test X-Injected: yes"]


def test_what_isnt_an_address_is_never_a_recipient():
    built = builder.build_message(spec(to=[LIN, "bob@example"], cc=["cy@"]), draft=True)
    assert (built["envelope"], built["to_disp"], built["cc_disp"]) == (["lin@example.test"], ["lin@example.test"], [])


@pytest.mark.parametrize("field", ["to", "cc", "bcc"])
def test_a_send_with_one_is_refused_naming_it(field):
    with pytest.raises(ValueError, match=f"^“bob@example” in {field.capitalize()} isn’t a valid address.$"):
        builder.build_message(spec(**{field: [LIN, "bob@example"] if field == "to" else ["bob@example"]}))


def test_a_draft_may_have_more_recipients_than_a_send():
    many = [f"p{i}@example.test" for i in range(MAX_RECIPIENTS + 1)]
    assert len(builder.build_message(spec(to=many), draft=True)["envelope"]) == MAX_RECIPIENTS + 1
    with pytest.raises(ValueError, match=f"^A message may have at most {MAX_RECIPIENTS} recipients.$"):
        builder.build_message(spec(to=many))


def test_a_send_to_no_one_is_refused_in_a_sentence():
    with pytest.raises(ValueError, match="^Add at least one recipient.$"):
        builder.build_message(spec(to=["", " "]))


def test_an_account_that_isnt_an_address_is_named_as_from():
    with pytest.raises(ValueError, match="^“ada@example” in From isn’t a valid address.$"):
        builder.build_message(spec(account="ada@example"), draft=True)
