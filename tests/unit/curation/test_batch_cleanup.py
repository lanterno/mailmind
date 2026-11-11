"""Cleaning up many senders at once: what it may name, what it shows before anyone says yes, and that one Undo takes it all back."""
import pytest

from mailmind.curation.domain import cleanup_plan

LOOM, THIMBLE = "news@loom.example.test", "orders@thimble.example.test"


# ── what a batch may name ────────────────────────────────────────────────────────────────────────

def test_a_batch_names_each_sender_once_as_rows_go_by_them():
    assert cleanup_plan.senders_named([" News@Loom.example.test", "news@loom.example.test", THIMBLE]) == [LOOM, THIMBLE]


@pytest.mark.parametrize("raw", [None, "x@y.test", [], ["nobody"], [LOOM, 5], [f"a{i}@example.test" for i in range(cleanup_plan.BATCH_MAX + 1)]])
def test_a_list_that_wont_read_names_nobody(raw):
    assert cleanup_plan.senders_named(raw) is None


def test_a_batch_shares_its_moves_out_in_order_and_says_when_some_are_left():
    read = [("a", list(range(300)), False), ("b", list(range(300)), False), ("c", [1, 2], False)]
    taken, capped = cleanup_plan.within_batch(read)
    assert [(a, len(t)) for a, t in taken] == [("a", 300), ("b", 200), ("c", 0)]
    assert capped is True


def test_a_batch_within_every_cap_leaves_nothing():
    taken, capped = cleanup_plan.within_batch([("a", [1, 2], False), ("b", [3], False)])
    assert (dict(taken), capped) == ({"a": [1, 2], "b": [3]}, False)


def test_a_sender_past_their_own_cap_counts_as_left_behind():
    assert cleanup_plan.within_batch([("a", [1], True)])[1] is True
