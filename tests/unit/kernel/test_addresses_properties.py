"""What holds of a typed recipient whatever a person types: nothing that could end a header line comes out, what's
accepted reads back as itself, and the only thing any of it raises is ValueError."""
import email.utils

import pytest
from hypothesis import assume, given, strategies as st

from mailmind.kernel import addresses
from property_runs import runs

# Every codepoint `clean` promises to take out: C0 and C1 controls, bidi and zero-width marks, line separators.
DANGEROUS = {*map(chr, range(0x20)), *map(chr, range(0x7f, 0xa0)), *map(chr, range(0x200b, 0x2010)),
             *map(chr, range(0x202a, 0x202f)), "\u2060", *map(chr, range(0x2066, 0x206a)), "\u2028", "\u2029", "\ufeff"}
NASTY = st.sampled_from(["\r", "\n", "\r\n", "\x00", "\t", "\x85", "\u2028", "\u202e", "\u200b", "\ufeff", "@", ",", ";", ":",
                         "<", ">", "(", ")", "[", "]", "\\", '"', " ", ".", "é", "日本", "ß", "\u3002", "xn--", "Bcc:"])
LOCAL = st.from_regex(r"[a-z0-9][a-z0-9._+-]{0,12}", fullmatch=True)
LABEL = st.from_regex(r"[a-z0-9]{1,10}", fullmatch=True)
UNICODE_LABEL = st.text(st.sampled_from("abcdéüßñ日本語"), min_size=1, max_size=6)
SPEC = st.builds(lambda local, labels: f"{local}@{'.'.join(labels)}.test", LOCAL,
                 st.lists(LABEL | UNICODE_LABEL, min_size=1, max_size=3))
NAME = st.text(st.sampled_from("abcXYZ019 ,.;:'-()<>@éß日本"), max_size=16)
# What a person might type, or paste: anything, a good address with trouble spliced into it, or the pieces of one.
SPLICED = st.builds(lambda spec, at, bad: spec[:at] + bad + spec[at:], SPEC, st.integers(0, 24), NASTY)
A_NAMED = st.builds(lambda name, spec: f"{name} <{spec}>", st.lists(NASTY | st.sampled_from(["Bo", "Ng"]), max_size=4).map("".join), SPEC)
PIECES = st.lists(st.sampled_from(["ada", "lin", "@", "example", ".", "test", "<", ">", '"', "Bo Ng", ","]) | NASTY, max_size=12).map("".join)
TYPED = st.text(st.characters(), max_size=40) | SPLICED | A_NAMED | PIECES
RAW = TYPED | st.none() | st.lists(TYPED | st.none(), max_size=4)
# The characters an address may not hold, in ASCII. Their fullwidth forms are the known bug at the end of this file.
NOT_IN_AN_ADDRESS = set(" \t,;:<>()[]\\\"")


def parsed(raw):
    """What parse_addr_list says of `raw`, or nothing for a refusal. It may refuse only with ValueError."""
    try:
        return addresses.parse_addr_list(raw)
    except ValueError:
        return []


@given(st.text(st.characters(), max_size=60) | st.lists(NASTY).map("".join) | st.none())
@runs(300)
def test_cleaning_a_header_value_leaves_nothing_dangerous(text):
    out = addresses.clean(text)
    assert not set(out) & DANGEROUS, repr(out)
    assert addresses.clean(out) == out


@given(RAW)
@runs(500)
def test_nothing_typed_brings_a_line_break_or_a_nul_out(raw):
    for name, spec in parsed(raw):
        assert not set(name) & DANGEROUS, repr(name)
        assert not set(spec) & (DANGEROUS | NOT_IN_AN_ADDRESS), repr(spec)
        assert spec.count("@") == 1, repr(spec)


@given(TYPED | st.none(), SPLICED | SPEC | TYPED | st.none())
@runs(500)
def test_a_pair_is_clean_or_refused(name, addr):
    try:
        out_name, spec = addresses.validate_pair(name, addr)
    except ValueError:
        return
    assert not set(out_name) & DANGEROUS, repr(out_name)
    assert not set(spec) & (DANGEROUS | NOT_IN_AN_ADDRESS), repr(spec)
    assert spec.count("@") == 1, repr(spec)


@given(st.text(st.characters(), max_size=40) | st.none() | st.lists(st.sampled_from("aé.-\u3002\x00\r\n日本"), max_size=70).map("".join))
@runs(400)
def test_a_domain_comes_out_ascii_or_raises_value_error(domain):
    try:
        out = addresses.idna_domain(domain)
    except ValueError:
        return
    assert out.isascii(), repr(out)


@given(SPEC)
@runs(300)
def test_what_is_accepted_reads_back_as_itself(spec):
    [(name, once)] = addresses.parse_addr_list(spec)
    assert name == "" and once.isascii() and once.count("@") == 1
    assert addresses.parse_addr_list(once) == [("", once)]
    assert addresses.parse_addr_list([once]) == [("", once)]


@given(NAME, SPEC)
@runs(300)
def test_a_name_and_an_address_read_back_as_a_header_writes_them(name, spec):
    assume(name.strip())
    [(_, once)] = addresses.parse_addr_list(spec)
    assert addresses.parse_addr_list(f'"{name}" <{once}>') == [(name.strip(), once)]


@given(st.lists(SPEC, min_size=1, max_size=5))
@runs(200)
def test_a_list_keeps_its_order_and_each_address_once_whatever_its_case(specs):
    once = [addresses.parse_addr_list(s)[0][1] for s in specs]
    doubled = [x for s in once for x in (s, s.upper())]
    out = [spec for _, spec in addresses.parse_addr_list(doubled)]
    assert [s.lower() for s in out] == list(dict.fromkeys(s.lower() for s in once))
    assert email.utils.getaddresses([", ".join(out)]) == [("", s) for s in out]


# IDNA turns the fullwidth or small form of a separator into the separator itself: one chip the person sees, and a header
# or an envelope that would read otherwise.
@pytest.mark.parametrize("typed", [
    "bo@example\uff0cother.test", "bo@example\uff20other.test", "bo@exa\uff1cmple.test", "bo@exa\uff1emple.test",
    "bo@exa\uff1bmple.test", "bo@exa\uff1ample.test", "bo@exa\uff08mple.test", "bo@exa\uff3bmple.test",
    "bo@exa\uff3cmple.test", "bo@exa\uff02mple.test", "bo@exa\ufe50mple.test", "bo@exa\ufe6bmple.test"])
def test_a_separator_that_only_idna_makes_is_refused_too(typed):
    with pytest.raises(ValueError):
        addresses.parse_addr_list(typed)
