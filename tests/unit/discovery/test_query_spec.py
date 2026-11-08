"""mailmind.discovery.domain.query: the search grammar, parsed. What a query finds is in
test_search_build.py and tests/golden/http_get/search_*.json; here is what a parsed query says about itself."""
import random
import string

import pytest

from mailmind.discovery.domain.query import MAX_CHARS, Clause, Kind, QuerySpec


def parsed(text):
    return list(QuerySpec.parse(text).clauses)


@pytest.mark.parametrize("text,want", [
    ("hello", Clause(Kind.TEXT, "hello", narrows=True)),
    ('"a phrase"', Clause(Kind.TEXT, "a phrase", narrows=True)),
    ("-hello", Clause(Kind.TEXT, "hello", negated=True, narrows=True)),
    ("from:lin", Clause(Kind.FIELD, "lin", "from", narrows=True)),
    ("FROM:Lin", Clause(Kind.FIELD, "Lin", "from", narrows=True)),
    ("is:UNREAD", Clause(Kind.FLAG, None, "unread", narrows=True)),
    ("is:read", Clause(Kind.FLAG, None, "read")),
    ("is:snoozed", Clause(Kind.SNOOZED)),
    ("is:newsletter", Clause(Kind.BUCKET, "newsletters/marketing", narrows=True)),
    ("is:personal", Clause(Kind.BUCKET, "personal/protected")),
    ("bucket:receipts", Clause(Kind.BUCKET, "receipts/transactional", narrows=True)),
    ("bucket:Anything", Clause(Kind.BUCKET, "anything", narrows=True)),
    ("category:Finance", Clause(Kind.CATEGORY, "finance", narrows=True)),
    ("label:Travel", Clause(Kind.LABEL, "Travel", narrows=True)),
    ('label:"Two words"', Clause(Kind.LABEL, "Two words", narrows=True)),
    ("-label:Travel", Clause(Kind.LABEL, "Travel", negated=True, narrows=True)),
    ("has:attachment", Clause(Kind.ATTACHMENT, narrows=True)),
    ("in:Sent Items", Clause(Kind.FOLDER, "Sent")),
    ("account:bea@example.test", Clause(Kind.ACCOUNT, "bea@example.test")),
    ("before:1970-01-02T00:00:00+00:00", Clause(Kind.BEFORE, 86400, narrows=True)),
    ("after:1970-01-02T00:00:00+00:00", Clause(Kind.AFTER, 86400, narrows=True)),
])
def test_the_first_clause(text, want):
    assert parsed(text)[0] == want


@pytest.mark.parametrize("text", ["", "   ", "before:someday"])
def test_what_says_nothing_narrows_nothing(text):
    assert not QuerySpec.parse(text).narrows()


def test_a_date_that_doesnt_read_as_one_is_not_a_word_to_search_for():
    assert parsed("before:someday") == []
    assert parsed("before:") == [Clause(Kind.TEXT, "before:", narrows=True)]


@pytest.mark.parametrize("text", ["from:lin", "lin", '"a phrase"', "is:unread", "is:flagged", "is:junk", "is:newsletter",
                                  "is:receipts", "is:spam", "bucket:x", "category:finance", "has:attachment",
                                  "after:2026-01-01", "before:2026-01-01", "in:INBOX from:lin", "-from:bea from:lin"])
def test_what_narrows_the_mailbox(text):
    assert QuerySpec.parse(text).narrows()


@pytest.mark.parametrize("text", ["in:INBOX", "account:ada@example.test", "in:INBOX account:ada@example.test", "is:read",
                                  "is:answered", "is:snoozed", "is:personal", "is:blocked", "is:other", "-from:lin",
                                  "-lin", "-is:junk", "-in:Trash", "-has:attachment", "-after:2026-01-01",
                                  "-is:unread -category:finance"])
def test_what_is_too_wide_for_a_bulk_command(text):
    assert not QuerySpec.parse(text).narrows()


def test_a_folder_named_is_one_asked_for():
    assert QuerySpec.parse("from:lin in:Archive").names_folder()
    assert not QuerySpec.parse("from:lin -in:Archive").names_folder()
    assert not QuerySpec.parse("from:lin").names_folder()


def test_asking_about_snoozed_mail_either_way():
    assert QuerySpec.parse("is:snoozed").asks_about_snoozed() and QuerySpec.parse("-is:snoozed").asks_about_snoozed()
    assert not QuerySpec.parse("from:lin").asks_about_snoozed()


def test_a_query_is_cut_at_the_limit():
    assert parsed("a" * (MAX_CHARS + 50)) == [Clause(Kind.TEXT, "a" * MAX_CHARS, narrows=True)]


def test_an_unbalanced_quote_still_parses():
    assert parsed('"lunch plans') == [Clause(Kind.TEXT, '"lunch', narrows=True), Clause(Kind.TEXT, "plans", narrows=True)]


def test_nothing_raises():
    rng = random.Random(11)
    alphabet = string.printable + "\x00å "
    for _ in range(3000):
        QuerySpec.parse("".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40))))
    assert QuerySpec.parse(None).clauses == ()
