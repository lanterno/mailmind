"""A message's identity, now computed by the Rust core, holds to what the Python app computed before it: the same
identity for the same message, or every message the database already names would get a second one.

`python_of` is that code, kept here as the oracle. core/crates/mm-kernel/tests/identity.rs pins a few of its answers
on the Rust side.
"""
from hashlib import sha256

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mailmind.mailbox.domain import identity


def python_of(message_id, from_addr, date_iso, subject):
    written = "".join((message_id or "").split())
    if written:
        return written
    return "~" + sha256("|".join((from_addr or "", date_iso or "", subject or "")).encode()).hexdigest()


# Every character Python splits at, among others, so a Message-ID made only of them is common.
SPACES = "".join(chr(c) for c in range(0x110000) if chr(c).isspace())
folded = st.text(alphabet=st.sampled_from(SPACES + "<>@.ab​é"), max_size=12)
part = st.none() | st.text(max_size=40) | folded


@settings(max_examples=2000, deadline=None)
@given(part, part, part, part)
def test_rust_names_every_message_as_python_did(message_id, from_addr, date_iso, subject):
    assert identity.of(message_id, from_addr, date_iso, subject) == python_of(message_id, from_addr, date_iso, subject)


def test_every_character_python_splits_at_is_left_out_of_a_message_id():
    assert identity.of(f"<a{SPACES}b@example.test>", None, None, None) == "<ab@example.test>"


@pytest.mark.parametrize("where", range(4))
def test_text_that_isnt_unicode_is_refused_as_python_refused_it(where):
    """A lone surrogate is what an undecodable header byte becomes. Python refused it when it hashed an envelope, and
    the database refuses it in a Message-ID, so neither ever named a message."""
    args = [None, "bo@example.test", "2026-11-02", "Hello"]
    args[where] = "caf\udce9"
    with pytest.raises(UnicodeEncodeError):
        identity.of(*args)
