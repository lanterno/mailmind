"""An assessment as a value: its importance always 1 to 5, and what it wasn't told left unknown."""
import dataclasses

import pytest

from mailmind.kernel.assessment import Assessment, importance_of


@pytest.mark.parametrize("told, reads", [
    (1, 1), (3, 3), (5, 5),
    (9, 5), (0, 1), (-3, 1),            # the schema's bounds don't reach the model: structured output drops them
    ("4", 4), (3.7, 3),                 # as int() reads them
    (None, 2), ("soon", 2), ([], 2), ({}, 2), (float("inf"), 2), (float("nan"), 2),
])
def test_an_importance_is_one_to_five_and_two_when_it_wont_read(told, reads):
    assert importance_of(told) == reads


def test_the_value_holds_its_importance_to_that():
    assert Assessment("s", 9, "info", "", "m").importance == 5
    assert Assessment("s", "soon", "info", "", "m").importance == 2


def test_what_it_was_not_told_is_unknown():
    a = Assessment("s", 3, "info", "", "m")
    assert (a.served_by, a.prompt_version, a.outcome) == (None, None, None)


def test_it_cannot_be_changed():
    with pytest.raises(dataclasses.FrozenInstanceError):
        Assessment("s", 3, "info", "", "m").importance = 9
