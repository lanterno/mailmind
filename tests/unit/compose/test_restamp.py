"""A message restamped as it leaves (compose.domain.builder.restamped): its own Date says the moment given, folded lines
and all, and nothing else changes. The Rust core's `builder::restamped` is held to the same cases."""
from datetime import datetime, timedelta, timezone

from mailmind.compose.domain.builder import restamped

AT = datetime.fromtimestamp(1_795_000_000, timezone.utc)


def test_its_own_date_alone_says_the_moment_given():
    raw = (b"From: ada@example.test\r\nDate: Mon, 02 Nov 2026\r\n 12:00:00 +0000\r\nX-Date: kept\r\n"
           b"Message-ID: <m1@example.test>\r\n\r\nDate: in the body\r\n")
    assert restamped(raw, AT) == (b"From: ada@example.test\r\nDate: Wed, 18 Nov 2026 11:06:40 +0000\r\nX-Date: kept\r\n"
                                  b"Message-ID: <m1@example.test>\r\n\r\nDate: in the body\r\n")


def test_a_date_named_in_lower_case_is_restamped_too():
    raw = b"date: Mon, 02 Nov 2026 12:00:00 +0000\r\nSubject: Hi\r\n\r\nHi\r\n"
    assert restamped(raw, AT) == b"Date: Wed, 18 Nov 2026 11:06:40 +0000\r\nSubject: Hi\r\n\r\nHi\r\n"


def test_a_message_with_no_date_is_as_it_was():
    raw = b"Subject: Hi\r\n\r\nDate: in the body\r\n"
    assert restamped(raw, AT) == raw


def test_a_moment_in_another_zone_is_said_in_utc():
    there = AT.astimezone(timezone(timedelta(hours=2)))
    assert b"Date: Wed, 18 Nov 2026 11:06:40 +0000\r\n" in restamped(b"Date: x\r\n\r\n", there)
