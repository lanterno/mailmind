"""A person's filters: what one may say, how they're kept in the rule book, and what POST /api/filters asks of the use case
(mailmind.curation.application.filters.Filters)."""
import json
import re

import pytest

from mailmind.bootstrap.curation import rules
from mailmind.curation.application.filters import Filters
from mailmind.curation.domain import filters, rulebook
from mailmind.discovery.domain import arrival
from mailmind.discovery.domain.query import MAX_CHARS
from mailmind.kernel.errors import NotFound, Refused
from stored_rows import document, put_document


# ── what one may say ────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q", ["from:lin", "subject:invoice", "to:ada", "cc:ada", "lunch", '"a phrase"', "is:newsletter", "bucket:spam",
                               "from:lin is:unread", "from:lin -subject:sale", "in:INBOX from:lin", "from:lin account:ada@example.test",
                               "from:lin -in:Archive"])
def test_what_can_be_judged_of_mail_as_it_arrives(q):
    assert arrival.refusal(q) is None


@pytest.mark.parametrize("q, says", [
    ("", "something to look for"),
    ("in:INBOX", "something to look for"),
    ("is:unread", "something to look for"),
    ("is:flagged", "something to look for"),
    ("account:ada@example.test", "something to look for"),
    ("-from:lin", "something to look for"),
    ("from:lin has:attachment", "attachments"),
    ("from:lin category:finance", "AI"),
    ("from:lin is:snoozed", "snoozed"),
    ("from:lin before:2026-01-01", "before:"),
    ("from:lin after:2026-01-01", "after:"),
    ("body:invoice", "body"),
    ("from:lin -body:sale", "body"),
    ("from:lin in:Archive", "in:INBOX"),
    ("from:lin in:inbox", "in:INBOX"),
    ("from:lin " + "x" * MAX_CHARS, "too long"),
])
def test_what_cant_says_why_in_words(q, says):
    assert says in arrival.refusal(q)


# ── what they ask of mail ───────────────────────────────────────────────────────────────────────

def test_what_filters_ask_together():
    star = filters.new("a", True, False)
    read = filters.new("b", False, True)
    assert filters.flags_of([]) == {}
    assert filters.flags_of([star]) == {"flagged": True}
    assert filters.flags_of([read]) == {"seen": True}
    assert filters.flags_of([star, read]) == {"flagged": True, "seen": True}


# ── the rule book ───────────────────────────────────────────────────────────────────────────────

def test_a_rule_book_from_before_filters_reads_as_none(data_dir):
    put_document("rules", json.dumps({"keep_senders": ["a@example.test"]}))
    r = rules.load()
    assert r["filters"] == [] and r["keep_senders"] == ["a@example.test"]


def test_a_change_of_the_lists_keeps_the_filters(data_dir):
    f = filters.new("from:lin", True, False)
    rules.change(lambda r: r.update(filters=[f]))
    rules.add("kept.example.test", "keep")
    rules.remove("kept.example.test")
    rules.set_expiry("old.example.test", 24)
    assert rules.load()["filters"] == [f]


def test_it_is_kept_with_the_lists(data_dir):
    f = filters.new("from:lin", True, False)
    rules.change(lambda r: r.update(filters=[f]))
    kept = json.loads(document("rules"))
    assert kept["filters"] == [f] and "keep_senders" in kept


@pytest.mark.parametrize("bad", [[{"q": "from:lin"}], [{"id": "f", "q": 3}], ["not a filter"], 3])
def test_a_filter_that_isnt_one_is_not_read_as_one(bad):
    with pytest.raises((ValueError, TypeError, AttributeError)):
        rulebook.normalized({"filters": bad})


def test_a_change_that_raises_saves_nothing(data_dir):
    def fails(r):
        r["filters"] = [filters.new("from:lin", True, False)]
        raise RuntimeError("no")
    with pytest.raises(RuntimeError):
        rules.change(fails)
    assert rules.load()["filters"] == []


# ── a change asked for (Filters.post), kept in the rule book ────────────────────────────────────────

GOOD = {"verb": "create", "q": "from:news@loom.example.test", "star": True, "read": False}


class Conditions:
    """Discovery's word on a query, as a filter asks for it: one about attachments can't be judged yet, and any other can."""
    def refusal(self, q):
        return "MailMind can't tell attachments of mail as it arrives." if "has:attachment" in q else None


def post(**body):
    return Filters(rules, Conditions()).post({**GOOD, **body})


def test_a_filter_is_made_with_an_id_of_its_own_and_kept(data_dir):
    [made] = post()["filters"]
    assert made["id"].startswith("f-") and {k: made[k] for k in ("q", "star", "read")} == {"q": GOOD["q"], "star": True, "read": False}
    assert rules.load()["filters"] == [made]


def test_an_update_says_it_anew_and_a_delete_takes_it_away(data_dir):
    [made] = post()["filters"]
    changed = {"id": made["id"], "q": "subject:invoice", "star": False, "read": True}
    assert post(verb="update", id=made["id"], q="subject:invoice", star=False, read=True)["filters"] == [changed]
    assert post(verb="delete", id=made["id"], q=None, star=None, read=None)["filters"] == []
    assert rules.load()["filters"] == []


def test_a_query_is_kept_as_typed(data_dir):
    assert post(q='  from:Lin "Thursday Lunch"  ')["filters"][0]["q"] == 'from:Lin "Thursday Lunch"'


@pytest.mark.parametrize("verb", ["create", "update"])
def test_a_query_a_filter_cant_use_is_refused_in_its_own_words_and_nothing_changes(data_dir, verb):
    [made] = post()["filters"]
    with pytest.raises(Refused, match="^MailMind can't tell attachments"):
        post(verb=verb, id=made["id"], q="from:lin has:attachment")
    assert rules.load()["filters"] == [made]


@pytest.mark.parametrize("verb", ["create", "update"])
def test_a_filter_must_do_something(data_dir, verb):
    [made] = post()["filters"]
    with pytest.raises(Refused, match="^Choose what the filter does"):
        post(verb=verb, id=made["id"], star=False, read=False)
    assert rules.load()["filters"] == [made]


@pytest.mark.parametrize("verb", ["update", "delete"])
def test_naming_a_filter_there_isnt_finds_none_and_changes_nothing(data_dir, verb):
    [made] = post()["filters"]
    with pytest.raises(NotFound, match="no such filter"):
        post(verb=verb, id="f-gone")
    assert rules.load()["filters"] == [made]


@pytest.mark.parametrize("verb", [None, "move"])
def test_a_verb_it_doesnt_know_is_refused(data_dir, verb):
    with pytest.raises(Refused, match=re.escape("verb must be create|update|delete")):
        post(verb=verb)
    assert rules.load()["filters"] == []


def test_it_keeps_a_ceiling(data_dir, monkeypatch):
    monkeypatch.setattr(filters, "MOST", 1)
    post()
    with pytest.raises(Refused, match=re.escape("That's as many filters as MailMind keeps (1).")):
        post(q="from:bea")
    assert len(rules.load()["filters"]) == 1
