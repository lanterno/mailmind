"""Whom a stored message went to (mailmind.kernel.addressees), from its To and Cc as the store keeps them: one decoded text with
a space between the two. The cases are tests/fixtures/addressees.json, which the Rust core's `mm_kernel::addressees` is held to
too, so both servers name the same people."""
import json
from pathlib import Path

import pytest

from mailmind.kernel import addressees

CASES = json.loads((Path(__file__).parents[2] / "fixtures" / "addressees.json").read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["why"] for c in CASES])
def test_each_person_is_read_once_in_order_with_the_name_written(case):
    assert [list(p) for p in addressees.people(case["text"])] == case["people"]


def test_a_row_carries_the_first_two_and_counts_the_rest():
    text = "Lin Mori <lin@example.test>, bea@example.test Cy <cy@example.test>, dee@example.test"
    assert addressees.listed(text) == {
        "sent_to": [{"name": "Lin Mori", "addr": "lin@example.test"}, {"name": None, "addr": "bea@example.test"}],
        "sent_to_more": 2}


@pytest.mark.parametrize("text, first, more", [
    ("Lin <lin@example.test>", 1, 0),
    ("Lin <lin@example.test> bea@example.test", 2, 0),
    ("", 0, 0),
    (None, 0, 0),
])
def test_a_row_of_fewer_counts_none_more(text, first, more):
    listed = addressees.listed(text)
    assert (len(listed["sent_to"]), listed["sent_to_more"]) == (first, more)


def test_the_same_person_twice_counts_once():
    assert addressees.listed("Lin <lin@example.test>, LIN@example.test, lin@example.test")["sent_to_more"] == 0
