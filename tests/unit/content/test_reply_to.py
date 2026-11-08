"""Where a reply goes: Reply-To's address, as the server read it, and never one a display name holds."""
import pytest

from mailmind.content import mime


def parsed(value):
    return mime.parse(b"From: bo@example.test\r\nReply-To: " + value + b"\r\nSubject: s\r\n\r\nhi\r\n")["reply_to"]


@pytest.mark.parametrize("value, address", [
    (b"desk@other.test", "desk@other.test"),
    (b"Help Desk <desk@other.test>", "desk@other.test"),
    (b'"boss@bank.test" <desk@other.test>', "desk@other.test"),                   # an address in a name is a name
    (b"=?utf-8?q?boss=40bank=2Etest?= <desk@other.test>", "desk@other.test"),     # however it was encoded
    (b"first@other.test, second@else.test", "first@other.test"),
    (b"Desk@Other.test", "Desk@Other.test"),                                      # as the header wrote it
])
def test_a_reply_to_is_the_address_it_names(value, address):
    assert parsed(value) == address


def test_a_reply_to_folded_across_lines_still_reads():
    assert parsed(b"A rather long name for a desk\r\n  <desk@other.test>") == "desk@other.test"


@pytest.mark.parametrize("value", [b"", b"no address here", b"<>"])
def test_a_reply_to_with_no_address_is_none(value):
    assert parsed(value) == ""


def test_no_reply_to_is_none():
    assert mime.parse(b"From: bo@example.test\r\nSubject: s\r\n\r\nhi\r\n")["reply_to"] == ""
