"""What autocomplete looks for and how a contact card reads, with no database and no clock."""
import pytest

from mailmind.discovery.domain import contacts


def ago(iso):
    return f"ago:{iso}"


def when(iso):
    return f"when:{iso}"


def card(agg=None, recent=()):
    a = {"n": 3, "name": "Lin Ma", "replied": 1, "last": "later", "first": "sooner", **(agg or {})}
    return contacts.card("lin@example.test", a, ["ada@example.test"], recent, ago, when)


@pytest.mark.parametrize("q, looks_for", [(None, ""), ("", ""), ("  Lin ", "lin"), ("L" * 80, "l" * 64)])
def test_autocomplete_looks_for_the_start_of_what_was_typed(q, looks_for):
    assert contacts.needle(q) == looks_for


def test_a_suggestion_with_no_name_has_an_empty_one():
    assert contacts.suggestion({"addr": "lin@example.test", "name": None, "n": 4}) == {"addr": "lin@example.test", "name": "", "n": 4}


@pytest.mark.parametrize("addr, a", [(" Lin@Example.test ", "lin@example.test"), ("a@", "a@")])
def test_a_card_is_for_anything_with_an_at_sign_in_lowercase(addr, a):
    assert contacts.address(addr) == a


@pytest.mark.parametrize("addr", [None, "", "   ", "lin"])
def test_nothing_else_has_a_card(addr):
    assert contacts.address(addr) is None


def test_a_card_says_how_you_stand():
    assert card() == {"ok": True, "addr": "lin@example.test", "name": "Lin Ma", "count": 3, "replied": 1, "known": True,
                      "last_ago": "ago:later", "first_ago": "ago:sooner", "accounts": ["ada@example.test"], "recent": []}


def test_someone_with_no_mail_is_not_known_and_has_no_dates():
    got = card({"n": None, "name": None, "replied": None, "last": None, "first": None})
    assert (got["count"], got["name"], got["replied"], got["known"], got["last_ago"], got["first_ago"]) == (0, "", 0, False, None, None)


def test_a_recent_thread_reads_as_a_list_does():
    recent = [{"pk": 7, "account": "ada@example.test", "subject": None, "thread_id": "gm:1", "date_iso": "then", "seen": 1}]
    got = card(recent=recent)["recent"]
    assert got == [{"pk": 7, "account": "ada@example.test", "subject": "(no subject)", "thread_id": "gm:1", "when": "when:then", "seen": True}]
    assert got[0]["seen"] is True
