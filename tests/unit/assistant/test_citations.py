"""A model may cite only what it was shown."""
from mailmind.assistant.domain import citations

SHOWN = {3, 7, 9}


def test_an_answer_cites_what_it_was_shown_in_the_order_it_named_them():
    assert citations.in_answer([9, 3], SHOWN) == [9, 3]


def test_an_answer_cannot_cite_what_it_was_not_shown():
    assert citations.in_answer([3, 999999, 8], SHOWN) == [3]


def test_a_message_is_cited_once():
    assert citations.in_answer([3, 3, "3"], SHOWN) == [3]


def test_an_id_may_come_as_a_number_in_a_string():
    assert citations.in_answer(["7"], SHOWN) == [7]


def test_what_does_not_read_as_an_id_is_passed_over():
    assert citations.in_answer(["seven", None, {"pk": 3}, [3], 7], SHOWN) == [7]


def test_a_reply_that_holds_no_list_cites_nothing():
    for told in (None, 3, "3", {"pk": 3}):
        assert citations.in_answer(told, SHOWN) == []


def test_a_group_holds_the_integers_it_was_shown():
    assert citations.in_group([3, "7", 9, 999999, None], SHOWN) == [3, 9]


def test_a_group_is_held_to_its_cap():
    shown = set(range(200))
    assert citations.in_group(list(range(200)), shown) == list(range(citations.GROUP_MAX))
