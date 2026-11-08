"""What a search decides around its QuerySpec, with no database and no clock."""
import pytest

from mailmind.discovery.domain import search
from mailmind.discovery.domain.query import QuerySpec
from mailmind.kernel.errors import Refused


def asked(**qs):
    return search.asked({k: [v] for k, v in qs.items()})


def row(pk=1, **kw):
    return {"pk": pk, "message_key": pk * 10, "account": "ada@example.test", "folder": "INBOX", "from_name": "Lin Ma",
            "from_addr": "lin@example.test", "subject": "Fabric", "date_iso": "2026-11-02T12:00:00+00:00", "snippet": "swatches",
            "seen": 1, "flagged": 0, "has_attach": 0, "is_junk": 0, "bucket": "PERSONAL", "thread_id": None, "importance": None, "category": None,
            "action": None, **kw}


def when(iso):
    return f"when:{iso}"


# -- what a request asks ----------------------------------------------------------

@pytest.mark.parametrize("qs", [{}, {"q": [""]}, {"q": ["   "]}, {"q": [None]}])
def test_nothing_typed_asks_nothing(qs):
    assert search.asked(qs) is None


def test_nothing_typed_answers_whatever_else_the_request_holds():
    assert search.asked({"q": [""], "limit": ["many"], "offset": ["far"]}) is None


def test_it_says_nothing_of_a_bad_number_past_that():
    with pytest.raises(Refused):
        asked(q="heron", limit="many")
    with pytest.raises(Refused):
        asked(q="heron", offset="far")


def test_the_answer_to_nothing_is_a_list_of_nothing_of_its_own():
    assert search.nothing_asked() == {"ok": True, "query": "", "items": [], "total": 0, "has_more": False}
    assert search.nothing_asked()["items"] is not search.nothing_asked()["items"]


def test_what_goes_unsaid_has_a_default():
    want = asked(q="  heron ")
    assert (want.q, want.scope, want.account, want.limit, want.offset) == ("heron", "account", None, 80, 0)
    assert want.spec == QuerySpec.parse("heron")


def test_a_request_says_what_it_says():
    want = asked(q="heron", scope="account", account="ada@example.test", limit="20", offset="40")
    assert (want.scope, want.account, want.limit, want.offset) == ("account", "ada@example.test", 20, 40)


def test_every_account_means_no_one_account_whichever_it_names():
    want = asked(q="heron", scope="all", account="ada@example.test")
    assert (want.scope, want.account) == ("all", None)


@pytest.mark.parametrize("limit, held", [("0", 1), ("-3", 1), ("1", 1), ("200", 200), ("201", 200)])
def test_a_page_is_held_to_what_can_be_shown(limit, held):
    assert asked(q="heron", limit=limit).limit == held


def test_an_offset_before_the_first_is_the_first():
    assert asked(q="heron", offset="-5").offset == 0


# -- one result a message ---------------------------------------------------------

def copies(*keys):
    return [{"pk": i, "message_key": k} for i, k in enumerate(keys, 1)]


def test_a_message_is_its_first_copy():
    assert [r["pk"] for r in search.first_copies(copies(7, 7, 8), 10, 0)] == [1, 3]


def test_a_row_with_no_key_is_a_message_of_its_own():
    assert [r["pk"] for r in search.first_copies(copies(None, None), 10, 0)] == [1, 2]


def test_an_offset_counts_messages_and_not_copies():
    assert [r["pk"] for r in search.first_copies(copies(7, 7, 8, 8, 9), 10, 1)] == [3, 5]


def test_it_keeps_one_past_the_limit_and_reads_no_further():
    read = []

    def rows():
        for r in copies(1, 2, 3, 4, 5):
            read.append(r["pk"])
            yield r

    assert [r["pk"] for r in search.first_copies(rows(), 2, 0)] == [1, 2, 3]
    assert read == [1, 2, 3]


def test_a_copy_of_one_already_past_counts_for_nothing():
    assert [r["pk"] for r in search.first_copies(copies(7, 7, 8), 1, 0)] == [1, 3]


# -- a hit ------------------------------------------------------------------------

ALIASES = {"ada@example.test": "Work"}


def test_a_hit_reads_as_the_list_shows_it():
    assert search.hit(row(flagged=1, has_attach=1, is_junk=1, seen=0), ALIASES, when) == {
        "key": 10, "account": "ada@example.test", "folder": "INBOX", "alias": "Work",
        "from_name": "Lin Ma", "from_addr": "lin@example.test", "subject": "Fabric",
        "when": "when:2026-11-02T12:00:00+00:00", "date": "2026-11-02", "snippet": "swatches",
        "seen": False, "flagged": True, "has_attach": True, "is_junk": True, "bucket": "PERSONAL", "thread_id": None,
        "importance": None, "action": None, "needs_you": False, "reply_worthy": False, "commitment_worthy": False, "safe_to_file": False,
    }


def test_a_flag_is_true_or_false_and_no_number():
    got = search.hit(row(seen=1, flagged=1, has_attach=1, is_junk=1), ALIASES, when)
    assert all(got[k] is True for k in ("seen", "flagged", "has_attach", "is_junk"))


def test_what_a_hit_lacks_reads_as_little_as_it_can():
    got = search.hit(row(from_name=None, subject=None, date_iso=None, snippet=None), {}, when)
    assert (got["from_name"], got["subject"], got["date"], got["snippet"], got["alias"]) == (
        "lin@example.test", "(no subject)", "", "", None)


def test_a_snippet_is_cut_short():
    assert search.hit(row(snippet="x" * 300), ALIASES, when)["snippet"] == "x" * 200


def test_the_verdict_on_a_hit_is_what_the_kernel_says():
    got = search.hit(row(importance=5, category="action-needed", action="reply"), ALIASES, when)
    assert (got["importance"], got["needs_you"], got["reply_worthy"]) == (5, True, True)


# -- a page -----------------------------------------------------------------------

def test_a_row_past_the_page_says_there_is_more_and_doesnt_show():
    answer = search.page([row(1), row(2), row(3)], asked(q="heron", limit="2", offset="4"), ALIASES, when, "now")
    assert [i["key"] for i in answer["items"]] == [10, 20]
    assert answer["has_more"] is True


def test_a_full_page_with_nothing_past_it_says_there_is_no_more():
    answer = search.page([row(1), row(2)], asked(q="heron", limit="2", offset="4", scope="all"), ALIASES, when, "now")
    assert {k: v for k, v in answer.items() if k != "items"} == {
        "ok": True, "query": "heron", "scope": "all", "offset": 4, "has_more": False, "generated": "now"}


# -- what a bulk command's filter holds -------------------------------------------

def test_every_message_is_held_by_key_and_folder_and_only_the_sample_is_shown():
    messages, sample = search.reached([row(1), row(2, folder="Archive"), row(3)], 2, when)
    assert messages == [(10, "INBOX"), (20, "Archive"), (30, "INBOX")]
    assert sample == [{"from": "Lin Ma", "subject": "Fabric", "when": "when:2026-11-02T12:00:00+00:00"}] * 2


def test_a_sample_says_as_little_as_nothing_when_it_must():
    _, sample = search.reached([row(from_name=None), row(from_name=None, from_addr=None, subject=None)], 2, when)
    assert sample[0]["from"] == "lin@example.test"
    assert (sample[1]["from"], sample[1]["subject"]) == ("", "(no subject)")
