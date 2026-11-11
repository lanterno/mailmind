"""A person's filters: what one may say, how they're kept in the rules file, and POST /api/filters."""
import json

import pytest

from mailmind.bootstrap.curation import rules
from mailmind.curation.domain import filters, rulebook
from mailmind.discovery.domain import arrival
from mailmind.discovery.domain.query import MAX_CHARS


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


# ── the file ────────────────────────────────────────────────────────────────────────────────────

def test_a_file_from_before_filters_reads_as_none(data_dir):
    with open(data_dir / "mailmind_rules.json", "w") as f:
        json.dump({"keep_senders": ["a@example.test"]}, f)
    assert rules.load()["filters"] == []


def test_a_change_of_the_lists_keeps_the_filters(data_dir):
    f = filters.new("from:lin", True, False)
    rules.change(lambda r: r.update(filters=[f]))
    rules.add("kept.example.test", "keep")
    rules.remove("kept.example.test")
    rules.set_expiry("old.example.test", 24)
    assert rules.load()["filters"] == [f]


def test_it_is_on_disk_with_the_lists(data_dir):
    f = filters.new("from:lin", True, False)
    rules.change(lambda r: r.update(filters=[f]))
    with open(data_dir / "mailmind_rules.json") as file:
        on_disk = json.load(file)
    assert on_disk["filters"] == [f] and "keep_senders" in on_disk


@pytest.mark.parametrize("bad", [[{"q": "from:lin"}], [{"id": "f", "q": 3}], ["not a filter"], 3])
def test_a_filter_that_isnt_one_is_not_read_as_one(bad):
    with pytest.raises((ValueError, TypeError, AttributeError)):
        rulebook.normalized({"filters": bad})


def test_a_bad_hand_edit_keeps_the_rules_read_well(data_dir):
    rules.add("kept.example.test", "keep")
    assert rules.load()["keep_domains"] == ["kept.example.test"]
    with open(data_dir / "mailmind_rules.json", "w") as f:
        json.dump({"keep_domains": ["other.example.test"], "filters": [{"nope": 1}]}, f)
    assert rules.load()["keep_domains"] == ["kept.example.test"]


def test_a_change_that_raises_saves_nothing(data_dir):
    def fails(r):
        r["filters"] = [filters.new("from:lin", True, False)]
        raise RuntimeError("no")
    with pytest.raises(RuntimeError):
        rules.change(fails)
    assert rules.load()["filters"] == []
