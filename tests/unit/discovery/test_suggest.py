"""What the search box offers as you type: the word being typed, finished from the grammar and from who has written to you."""

import pytest

from mailmind.discovery.domain import query, suggest
from mailmind.discovery.domain.query import Kind, QuerySpec

SAMPLES = {"before": "2026-01-01", "after": "2026-01-01", "is": "unread", "has": "attachment"}


# ── the word being typed ────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("box, settled, minus, name, value", [
    ("fro", "", "", None, "fro"),
    ("is:unread fro", "is:unread ", "", None, "fro"),
    ("from:ada", "", "", "from", "ada"),
    ("-from:ada", "", "-", "from", "ada"),
    ("-fro", "", "-", None, "fro"),
    ("FROM:Ada", "", "", "from", "Ada"),
    ('"a phrase" sub', '"a phrase" ', "", None, "sub"),
    ("from:", "", "", "from", ""),
])
def test_it_reads_the_word_as_the_parser_would(box, settled, minus, name, value):
    assert suggest.typing(box) == suggest.Typing(settled, minus, name, value)


@pytest.mark.parametrize("box", ["", "   ", "from:ada ", "? when is lunch", "!archive these", '"an open phrase', 'a "quoted bit"',
                                 "it's", "re:budget", "x" * (query.MAX_CHARS + 1), None])
def test_with_nothing_to_finish_it_offers_nothing(box):
    assert suggest.typing(box) is None


@pytest.mark.parametrize("t, needle", [
    (suggest.Typing("", "", None, "f"), None),              # a letter is too little to read everyone for
    (suggest.Typing("", "", None, "fr"), "fr"),
    (suggest.Typing("", "", "from", ""), ""),               # a bare from: asks who writes most
    (suggest.Typing("", "", "to", "ADA"), "ada"),
    (suggest.Typing("", "", "cc", "x" * 200), "x" * 64),
    (suggest.Typing("", "", "subject", "lunch"), None),
    (suggest.Typing("", "", "is", "unread"), None),
])
def test_it_reads_who_has_written_only_where_they_have_a_part(t, needle):
    assert suggest.needle(t) == needle


# ── held to the grammar ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", query.OPERATORS)
def test_every_operator_offered_is_one_the_parser_reads(name):
    clause = QuerySpec.parse(f"{name}:{SAMPLES.get(name, 'word')}").clauses[0]
    assert clause.kind is not Kind.TEXT


@pytest.mark.parametrize("value", query.IS_OFFERED)
def test_every_is_value_offered_is_one_the_parser_reads(value):
    assert QuerySpec.parse(f"is:{value}").clauses[0].kind is not Kind.TEXT


@pytest.mark.parametrize("value", query.HAS_OFFERED)
def test_every_has_value_offered_is_one_the_parser_reads(value):
    assert QuerySpec.parse(f"has:{value}").clauses[0].kind is Kind.ATTACHMENT


def test_the_parser_reads_no_operator_the_box_does_not_offer():
    """A new operator joins OPERATORS, or the box never offers it."""
    for name in query.FIELDS:
        assert name in query.OPERATORS


# ── what it offers ──────────────────────────────────────────────────────────────────────────────

def test_an_operator_waits_for_its_value():
    t = suggest.typing("fr")
    assert suggest.offers(t, [], []) == [{"value": "from:", "label": "from:", "detail": "", "complete": False}]


def test_a_value_keeps_what_was_typed_before_it_and_can_be_searched():
    items = suggest.offers(suggest.typing("in:INBOX is:un"), [], [])
    assert items == [{"value": "in:INBOX is:unread ", "label": "is:unread", "detail": "", "complete": True}]


def test_a_negation_stays_negated():
    assert suggest.offers(suggest.typing("-is:unr"), [], [])[0]["value"] == "-is:unread "
    assert suggest.offers(suggest.typing("-fr"), [], [])[0]["value"] == "-from:"


def test_a_sender_finishes_the_operator_typed_else_from():
    who = [{"addr": "ada@example.test", "name": "Ada", "n": 2}]
    assert suggest.offers(suggest.typing("to:ad"), who, [])[0]["value"] == "to:ada@example.test "
    assert suggest.offers(suggest.typing("ad"), who, [])[-1]["value"] == "from:ada@example.test "


def test_a_stranger_cant_put_a_space_or_a_quote_into_the_box():
    who = [{"addr": 'not one@example.test', "name": "", "n": 9}, {"addr": 'a"b@example.test', "name": "", "n": 9}]
    assert suggest.offers(suggest.typing("from:a"), who, [{"domain": "x y.test", "n": 1}]) == []


def test_how_many_of_each_it_offers_is_held():
    who = [{"addr": f"p{i}@example.test", "name": "", "n": 1} for i in range(8)]
    where = [{"domain": f"d{i}.example.test", "n": 1} for i in range(8)]
    items = suggest.offers(suggest.typing("from:p"), who, where)
    assert len(items) == 5 + 3
