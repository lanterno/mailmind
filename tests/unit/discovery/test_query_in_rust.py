"""The search grammar, now parsed by the Rust core, holds to what the Python app parsed before it: the same clauses for
the same text, or a saved search, a filter or a bulk command's plan would find other mail than it did.

`python_parse` is that code, kept here as the oracle, with the words it had. core/crates/mm-discovery/tests/query.rs
pins a few of its answers on the Rust side.
"""
import os
import shlex
import time
from datetime import datetime

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mailmind.discovery.domain import query
from mailmind.discovery.domain.query import Clause, Kind, QuerySpec

# -- the Python app's parser, as it was ---------------------------------------------------------------------------

MAX_CHARS = 512
FIELDS = frozenset({"from", "subject", "body", "to", "cc"})
FLAGS = frozenset({"unread", "read", "flagged", "answered", "junk"})
BUCKET_ALIAS = {
    "newsletter": "newsletters/marketing", "newsletters": "newsletters/marketing",
    "marketing": "newsletters/marketing",
    "receipt": "receipts/transactional", "receipts": "receipts/transactional",
    "transactional": "receipts/transactional",
    "notification": "automated-notifications", "notifications": "automated-notifications",
    "automated": "automated-notifications",
    "spam": "spam/scam", "scam": "spam/scam", "blocked": "blocked",
    "personal": "personal/protected", "protected": "personal/protected", "other": "other/personal-ish",
}
OPERATORS = ("from", "to", "cc", "subject", "body", "is", "has", "in", "label", "account", "before", "after", "bucket", "category")
IS_OFFERED = ("unread", "read", "flagged", "answered", "junk", "snoozed", "newsletter", "receipt", "notification")
HAS_OFFERED = ("attachment",)
_NARROW_IS = frozenset({"newsletter", "newsletters", "marketing", "receipt", "receipts", "transactional",
                        "notification", "notifications", "automated", "spam", "scam", "flagged", "unread", "junk"})


def _seconds(text):
    try:
        return int(datetime.fromisoformat(text).timestamp())
    except Exception:
        return None


def _clause(token):
    negated = token.startswith("-") and len(token) > 1
    if negated:
        token = token[1:]
    name, sep, value = token.partition(":")
    name, lower = name.lower(), value.lower()
    if sep and name in FIELDS and value:
        return Clause(Kind.FIELD, value, name, negated, narrows=True)
    if sep and name == "is" and lower in FLAGS:
        return Clause(Kind.FLAG, None, lower, negated, narrows=lower in _NARROW_IS)
    if sep and name == "is" and lower == "snoozed":
        return Clause(Kind.SNOOZED, negated=negated)
    if sep and name == "is" and lower in BUCKET_ALIAS:
        return Clause(Kind.BUCKET, BUCKET_ALIAS[lower], None, negated, narrows=lower in _NARROW_IS)
    if sep and name == "bucket" and value:
        return Clause(Kind.BUCKET, BUCKET_ALIAS.get(lower, lower), None, negated, narrows=True)
    if sep and name == "category" and value:
        return Clause(Kind.CATEGORY, lower, None, negated, narrows=True)
    if sep and name == "has" and lower == "attachment":
        return Clause(Kind.ATTACHMENT, negated=negated, narrows=True)
    if sep and name == "in" and value:
        return Clause(Kind.FOLDER, value, None, negated)
    if sep and name == "label" and value:
        return Clause(Kind.LABEL, value, None, negated, narrows=True)
    if sep and name == "account" and value:
        return Clause(Kind.ACCOUNT, value, None, negated)
    if sep and name in ("before", "after") and value:
        seconds = _seconds(value)
        if seconds is None:
            return None
        return Clause(Kind.BEFORE if name == "before" else Kind.AFTER, seconds, None, negated, narrows=True)
    if token.strip():
        return Clause(Kind.TEXT, token, None, negated, narrows=True)
    return None


def python_parse(text):
    text = (text or "").strip()[:MAX_CHARS]
    try:
        tokens = shlex.split(text)
    except ValueError:
        tokens = text.split()
    return tuple(c for c in map(_clause, tokens) if c)


# -- what queries are made of --------------------------------------------------------------------------------------

# Every character Python's split() and strip() take for space; shlex parts words at only four of them.
SPACES = "".join(chr(c) for c in range(0x110000) if chr(c).isspace())
# Characters Rust and Python could disagree about: Unicode 17's additions with a case (Rust knows them, Python 3.14
# doesn't), U+0295, which lost its case in 17, a capital sigma and what may stand around one, a Kelvin sign that
# lowercases to ASCII, a dotted capital I that lowercases to two characters, lone surrogates and the private-use
# characters the Rust side stands them in with.
TRICKY = ("꟎꟒꟔\U00016ea0\U00016ebb᫏ʕΣσςͅ'.­Kİßé日‮​"
          "\udce9𐏿\U00100000\U00100001\U0010fffd\x00")
OPERATOR = st.sampled_from([*OPERATORS, "IS", "Is", "BUCKET", "Category", "FROM", "Before", "AFTER", "x", ""])
VALUE = st.sampled_from([*FLAGS, *BUCKET_ALIAS, "snoozed", "attachment", "UNREAD", "Newsletter", "ΣΑΣ", "marKeting",
                         "Archive", "ada@example.test", "a b", ""])
LETTERS = st.text(st.sampled_from("ab:-\"'\\" + TRICKY + SPACES[:6] + "　\x1c"), max_size=8)
ANY_TEXT = st.text(st.characters(), max_size=12)


def digits(n):
    return st.text(st.sampled_from("0123456789"), min_size=n, max_size=n)


def two(*likely):
    return st.sampled_from(likely) | digits(2)


YEAR = st.sampled_from(["0000", "0001", "1425", "1969", "1970", "2026", "2514", "9998", "9999"]) | digits(4)
DATE = st.one_of(
    st.builds("{}-{}-{}".format, YEAR, two("00", "01", "02", "11", "12", "13"), two("00", "01", "28", "29", "30", "31")),
    st.builds("{}{}{}".format, YEAR, two("01", "11", "12"), two("01", "02", "31")),
    st.builds("{}-W{}".format, YEAR, two("00", "01", "45", "52", "53")),
    st.builds("{}-W{}-{}".format, YEAR, two("01", "45", "52", "53"), digits(1)),
    st.builds("{}W{}".format, YEAR, two("01", "45", "53")),
    st.builds("{}W{}{}".format, YEAR, two("01", "45", "53"), digits(1)),
)
SEPARATOR = st.sampled_from(["T", " ", "t", "é", " ", "日", "\U00100000", "\udce9", "-", "1", ":", "\x00", ""])
HH, MM = two("00", "09", "12", "23", "24"), two("00", "30", "59", "60")
FRACTION = st.text(st.sampled_from("0123456789"), min_size=1, max_size=9).map(lambda f: f.replace("0", "9", 2))
TIME = st.one_of(
    HH, st.builds("{}:{}".format, HH, MM), st.builds("{}{}".format, HH, MM), st.builds("{}:{}:{}".format, HH, MM, MM),
    st.builds("{}{}{}".format, HH, MM, MM),
    st.builds("{}:{}:{}{}{}".format, HH, MM, MM, st.sampled_from([".", ",", ""]), FRACTION),
    st.builds("{}:{}{}".format, HH, st.sampled_from([":", ".", ""]), st.sampled_from(["Z", "+", ""])),
)
OFFSET = st.one_of(
    st.just(""), st.just("Z"),
    st.builds("{}{}".format, st.sampled_from("+-"), two("00", "05", "23", "24")),
    st.builds("{}{}:{}".format, st.sampled_from("+-"), two("00", "05", "23", "24"), two("00", "30", "59")),
    st.builds("{}{}{}".format, st.sampled_from("+-"), two("00", "05", "23"), two("00", "30", "59")),
    st.builds("{}{}:{}:{}{}".format, st.sampled_from("+-"), two("00", "23"), two("00", "59"), two("00", "59"),
              st.sampled_from(["", ".5", ".999999", ".1234567"])),
)
TAIL = st.sampled_from(["", "", "", "X", "é", "\udce9", "\U00100000", "\x00junk", "\x00+05:00", "Z", ":"])


def between(low, high):
    return st.integers(low, high).map(lambda n: f"{n:0{len(str(high))}d}")


# Real times to the microsecond, far from 1970 too, where the float a second is counted through can't hold them.
REAL = st.builds("{}-{}-{}T{}:{}:{}.{}{}".format,
                 st.sampled_from(["0001", "1425", "1426", "2514", "2515", "9999"]) | between(1, 9999).map("{:0>4}".format),
                 between(1, 12).map("{:0>2}".format), between(1, 28), between(0, 23), between(0, 59), between(0, 59),
                 st.sampled_from(["999999", "99999", "9999", "99998", "000001", "5"]) | digits(6),
                 st.sampled_from(["", "Z", "+00:00", "-00:00:00.5", "+23:59", "-23:59:59.999999", "+05:30", "-01:00"]))
# Python read anything between six digits of a fraction and an offset, but a surrogate, which it couldn't encode.
LENIENT = st.sampled_from(["", "", "", "X", "\udce9", "\x00"])
WRITTEN = DATE | REAL | st.builds("{}{}{}{}{}{}".format, DATE, SEPARATOR, TIME, LENIENT, OFFSET, TAIL) | \
    st.text(st.sampled_from("0123456789-WTZ:.,+ \x00é\udce9"), min_size=6, max_size=24)
A_DATE = st.builds("{}{}:{}".format, st.sampled_from(["", "-"]),
                   st.sampled_from(["before", "after", "BEFORE", "After"]), WRITTEN)


def operator_and_value(value):
    return st.builds("{}{}:{}".format, st.sampled_from(["", "-", "--"]), OPERATOR, value)


def quoted(word):
    return st.builds("{0}{1}{0}".format, st.sampled_from(['"', "'"]), word) | word.map(shlex.quote)


WORD = st.one_of(
    operator_and_value(VALUE | LETTERS), A_DATE, LETTERS, ANY_TEXT, quoted(LETTERS | VALUE),
    st.builds("{}{}".format, OPERATOR.map(lambda o: o + ":"), quoted(LETTERS | VALUE)),
    st.sampled_from(['"', "'", "\\", "-", "--", '""', "''", '"\\"', '"\\', "a\\", "\\ ", "-\\ "]),
)
GAP = st.text(st.sampled_from(SPACES), min_size=1, max_size=3)
QUERY = st.builds(lambda words, gaps: "".join(w + g for w, g in zip(words, gaps)),
                  st.lists(WORD, max_size=8), st.lists(GAP, min_size=8, max_size=8)) | \
    st.builds(str.__add__, GAP, st.lists(WORD, max_size=6).map(" ".join))
# Past the limit, with the cut landing in a quote, after a backslash or before a space.
TOO_LONG = st.builds(str.__add__, st.text(st.sampled_from('ab -":\\Σ\udce9　'), min_size=MAX_CHARS - 4,
                                          max_size=MAX_CHARS + 4), QUERY)


# -- the comparisons ------------------------------------------------------------------------------------------------

def rust_parse(text):
    return QuerySpec.parse(text).clauses


@settings(max_examples=2000, deadline=None)
@given(QUERY)
def test_rust_parses_every_query_as_python_did(text):
    assert rust_parse(text) == python_parse(text)


@settings(max_examples=2000, deadline=None)
@given(A_DATE)
def test_rust_reads_every_date_as_python_did(text):
    assert rust_parse(text) == python_parse(text)


@settings(max_examples=300, deadline=None)
@given(TOO_LONG)
def test_rust_cuts_a_long_query_where_python_did(text):
    assert rust_parse(text) == python_parse(text)


@pytest.fixture(params=["America/New_York", "Asia/Kolkata", "Australia/Lord_Howe", "Pacific/Kiritimati"])
def machine_zone(request):
    """A date with no zone is this machine's: Lord Howe moves half an hour, Kiritimati is fourteen hours ahead."""
    os.environ["TZ"] = request.param
    time.tzset()
    yield request.param
    os.environ["TZ"] = "UTC"
    time.tzset()


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(A_DATE | st.builds("after:{}-{}-{}T{}:{}".format, YEAR, two("03", "04", "10"), two("01", "05", "06"),
                          two("01", "02", "03"), two("00", "30")))
def test_a_date_with_no_zone_is_placed_in_this_machines_as_python_placed_it(machine_zone, text):
    assert rust_parse(text) == python_parse(text)


def test_none_and_nothing_parse_to_no_clauses():
    assert rust_parse(None) == python_parse(None) == ()
    assert rust_parse("") == rust_parse(SPACES) == ()


@pytest.mark.parametrize("space", SPACES)
def test_every_character_python_takes_for_space_parts_words_as_it_did(space):
    for text in (f"a{space}b", f"{space}a{space}", f'"a{space}b', f"-{space}", f'"{space}"', f"from:{space}x"):
        assert rust_parse(text) == python_parse(text), repr(text)


def _chunks(characters, size):
    return ["".join(characters[i:i + size]) for i in range(0, len(characters), size)]


# Every character but the two that would end the quote around them, and every surrogate.
EVERY_CHARACTER = [chr(c) for c in range(0x110000) if chr(c) not in '"\\']


def test_every_character_lowercases_as_python_lowercased_it():
    """A category is the word lowercased, so its value is what lower() made of it."""
    for chunk in _chunks(EVERY_CHARACTER, 500):
        assert rust_parse(f'category:"{chunk}"') == (Clause(Kind.CATEGORY, chunk.lower(), narrows=True),), chunk


def test_every_character_decides_a_final_sigma_as_python_did():
    """A capital sigma lowercases to a final one when a cased character comes before it and none after, past any
    case-ignorable ones. Each character is tried before a sigma, and on both sides of one after a cased letter."""
    for chunk in _chunks(EVERY_CHARACTER, 50):
        word = "".join(f"1{c}Σ1A{c}Σ{c}1" for c in chunk)
        assert rust_parse(f'category:"{word}"') == (Clause(Kind.CATEGORY, word.lower(), narrows=True),), chunk


def test_the_grammars_words_are_the_ones_python_had():
    assert (query.MAX_CHARS, query.FIELDS, query.FLAGS, query.BUCKET_ALIAS) == (MAX_CHARS, FIELDS, FLAGS, BUCKET_ALIAS)
    assert (query.OPERATORS, query.IS_OFFERED, query.HAS_OFFERED) == (OPERATORS, IS_OFFERED, HAS_OFFERED)


@pytest.mark.parametrize("text", [
    "before:9999-12-31T23:59:59.99999+00:00",       # the nearest float to the quotient is the next second
    "before:9999-12-31T23:59:59.999999",
    "after:0001-01-01T00:00:00.999999+00:00",
    "after:1969-12-31T23:59:59.5",                   # int() cuts toward zero, so this is 0, not -1
    "before:2026-11-00T24:00",                       # 24:00 on the 0th of a month is the 1st
    "before:2026-00-00T24:00",                       # and in month 0, January
    "before:0000-12-31T24:00",                       # a year 0 that midnight carries into year 1
    "before:0001-01-01",                             # which the zone can't place
    "after:2026-11-02T12:30:Z",
    "after:2026-11-02T12:30:45.123456garbage+05:00", # anything between six digits of a fraction and an offset
    "after:2026-11-02T12:30:45.123456\x00junk",      # and anything after a NUL
    "after:2026-11-02T12:00+00:00:00.5",             # a zero offset drops its microseconds
    "after:2026-11-02T12:00+23:59:59.999999",
    "after:2026-11-02T12:00-24:00",
    "after:2026W45112",                              # a week date whose separator is a digit
    "after:2026-11-02\udce912:00",                   # a surrogate between the date and the time reads as T
    "after:2026-11-02T12:00:00.123456\udce9+05:00",  # and anywhere else as no date
    "after:2026-11-02\udce9\udce9:00",
    "after:2026-11-02\udce912:00:00.123456\udce9+05:00",     # one surrogate as the separator, but two
    "category:\udce9Σ \U00100000 bucket:\U00100000\udce9",
])
def test_the_corners_python_read_its_own_way(text):
    assert rust_parse(text) == python_parse(text)
