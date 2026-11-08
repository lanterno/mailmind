"""What the Document Hub shows, with no database and no clock."""
import pytest

from mailmind.discovery.domain import documents
from mailmind.discovery.domain.documents import Asked


def row(pk=1, filename="plan.pdf", content_type="application/pdf", **kw):
    return {"pk": pk, "message_key": pk, "folder": "INBOX", "uid": pk, "part_id": "2", "filename": filename, "content_type": content_type, "size": 10, "message_id": f"<{pk}@example.test>",
            "account": "ada@example.test", "from_name": "Lin Ma", "from_addr": "lin@example.test", "subject": "Fabric", "date_iso": "then", **kw}


def page(rows, **want):
    return documents.page(rows, Asked(**want), lambda iso: f"when:{iso}")


# -- what a request asks ----------------------------------------------------------

@pytest.mark.parametrize("qs, limit, offset", [
    ({}, 400, 0),
    ({"limit": ["10"], "offset": ["5"]}, 10, 5),
    ({"limit": ["0"]}, 1, 0), ({"limit": ["-3"]}, 1, 0), ({"limit": ["5000"]}, 1000, 0),
    ({"offset": ["-3"]}, 400, 0),
    ({"limit": ["many"], "offset": ["far"]}, 400, 0),
])
def test_a_page_is_held_to_what_can_be_shown_and_a_number_that_wont_read_is_the_default(qs, limit, offset):
    want = documents.asked(qs)
    assert (want.limit, want.offset) == (limit, offset)


def test_it_asks_for_a_kind_and_words_in_lowercase():
    want = documents.asked({"kind": ["pdf"], "q": ["  Fabric "]})
    assert (want.kind, want.needle) == ("pdf", "fabric")


# -- one file, once -----------------------------------------------------------------

def test_one_email_synced_to_two_folders_is_one_file():
    both = [row(pk=1, message_id="<same@example.test>"), row(pk=2, message_id="<same@example.test>")]
    assert [d["key"] for d in page(both)["docs"]] == [1]


def test_two_files_of_one_message_arent():
    two = [row(pk=1, message_id="<same@example.test>"), row(pk=1, message_id="<same@example.test>", filename="other.pdf"),
           row(pk=1, message_id="<same@example.test>", size=11)]
    assert len(page(two)["docs"]) == 3


def test_a_name_compares_in_lowercase():
    two = [row(pk=1, message_id="<same@example.test>"), row(pk=2, message_id="<same@example.test>", filename="PLAN.pdf")]
    assert len(page(two)["docs"]) == 1


def test_with_no_message_id_it_goes_by_the_message():
    assert [d["key"] for d in page([row(pk=1, message_id=None), row(pk=2, message_id=None)])["docs"]] == [1, 2]
    assert [d["key"] for d in page([row(pk=2, message_id=None), row(pk=2, message_id=None, size=99)])["docs"]] == [2]


# -- what shows ---------------------------------------------------------------------

def test_what_isnt_a_document_counts_nowhere():
    answer = page([row(content_type="text/calendar", filename="invite.ics"), row(content_type="application/pgp-signature", filename="s.asc")])
    assert (answer["total"], answer["matched"], answer["facets"], answer["docs"]) == (0, 0, {}, [])


def test_facets_count_every_kind_whatever_the_page_wants():
    answer = page([row(pk=1), row(pk=2, filename="a.png", content_type="image/png")], kind="pdf")
    assert answer["facets"] == {"pdf": 1, "image": 1} and answer["total"] == 2
    assert (answer["matched"], [d["key"] for d in answer["docs"]]) == (1, [1])


@pytest.mark.parametrize("needle, found", [
    ("plan", True), ("lin ma", True), ("lin@example", True), ("fabric", True), ("nothing", False), ("", True),
])
def test_the_words_may_be_in_its_name_its_sender_or_its_subject(needle, found):
    assert (page([row()], needle=needle)["matched"] == 1) is found


def test_facets_count_those_the_words_left_out_too():
    answer = page([row()], needle="nothing")
    assert (answer["facets"], answer["total"], answer["matched"]) == ({"pdf": 1}, 1, 0)


def test_a_page_is_the_limit_from_the_offset_of_those_that_match():
    answer = page([row(pk=i) for i in range(1, 6)], limit=2, offset=1)
    assert [d["key"] for d in answer["docs"]] == [2, 3]
    assert (answer["matched"], answer["shown"], answer["total"]) == (5, 2, 5)


def test_a_document_says_where_to_download_it_and_says_little_as_nothing():
    doc = page([row(pk=7, part_id="1.2 a", size=None, from_name=None, subject=None)])["docs"][0]
    assert doc == {"key": 7, "folder": "INBOX", "uid": 7, "part_id": "1.2 a", "filename": "plan.pdf", "kind": "pdf", "size": 0, "account": "ada@example.test",
                   "from": "lin@example.test", "subject": "(no subject)", "when": "when:then",
                   "url": "/api/message/attachment?key=7&folder=INBOX&uid=7&part_id=1.2+a", "preview": None}


def test_a_sender_with_no_name_or_address_is_empty():
    assert page([row(from_name=None, from_addr=None)])["docs"][0]["from"] == ""


def test_nothing_stored_is_the_empty_answer():
    assert page([]) == {"ok": True, "total": 0, "matched": 0, "shown": 0, "facets": {}, "docs": []}
