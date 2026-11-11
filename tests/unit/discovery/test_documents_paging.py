"""The Document Hub's list: the server pages it and filters it, so a search reaches every file, and shows it in sections when
asked. What the page comes to (mailmind.discovery.domain.documents.page) from the rows the store gives, newest first, with no
database and no clock. That the store gives them in one order, so paging over requests covers every file once, is
tests/integration/discovery/test_documents_paging.py."""
from typing import get_args

import pytest

from mailmind.discovery.domain import documents


def doc(n, filename, sender=("Ana", "ana@example.test"), content_type="application/pdf", subject="Fabric"):
    name, addr = sender
    return {"pk": n, "message_key": n, "folder": "INBOX", "uid": n, "part_id": "2", "filename": filename, "content_type": content_type,
            "size": 1000 + n, "message_id": f"<m{n}@example.test>", "account": "ada@example.test", "from_name": name,
            "from_addr": addr, "subject": subject, "date_iso": "then"}


def reports(n, stem="report"):
    return [doc(i, f"{stem}-{i:03}.pdf") for i in range(n)]


def page(rows, **asked):
    return documents.page(rows, documents.asked({k: [v] for k, v in asked.items()}), lambda iso: f"when:{iso}")


def names(answer):
    return [d["filename"] for d in answer["docs"]]


# -- pages, kinds and words ----------------------------------------------------------------------------------------

def test_past_the_end_is_an_empty_page_that_still_counts_them_all():
    answer = page(reports(3), offset="100000")
    assert (answer["docs"], answer["shown"], answer["matched"], answer["total"]) == ([], 0, 3, 3)


@pytest.mark.parametrize("bad", ["nope", "-5", ""])
def test_an_offset_that_makes_no_sense_means_the_start(bad):
    assert page(reports(3), offset=bad)["docs"] == page(reports(3))["docs"]


def test_a_search_reaches_past_the_first_page():
    rows = reports(30) + [doc(99, "needle-000.pdf")]
    assert "needle-000.pdf" not in names(page(rows, limit="5"))
    found = page(rows, limit="5", q="NEEDLE")
    assert (names(found), found["matched"]) == (["needle-000.pdf"], 1)


def test_a_search_that_finds_nothing_says_so_and_keeps_the_counts():
    every, found = page(reports(3)), page(reports(3), q="nothing-is-called-this")
    assert (found["docs"], found["matched"]) == ([], 0)
    assert (found["total"], found["facets"]) == (every["total"], every["facets"])


def test_a_kind_gives_that_kind_and_pages_within_it():
    rows = [r for i in range(12) for r in (doc(i, f"report-{i:03}.pdf"), doc(100 + i, f"photo-{i:03}.png", content_type="image/png"))]
    images = page(rows, kind="image", limit="5")
    assert (images["matched"], images["shown"], {d["kind"] for d in images["docs"]}) == (12, 5, {"image"})
    assert images["facets"] == page(rows)["facets"] == {"pdf": 12, "image": 12}
    assert names(page(rows, kind="image", limit="5", offset="5")) == [f"photo-{i:03}.png" for i in range(5, 10)]


def test_a_kind_and_a_search_both_hold():
    rows = [doc(1, "same-000.pdf"), doc(2, "same-000.png", content_type="image/png"), doc(3, "other-000.png", content_type="image/png")]
    assert names(page(rows, kind="image", q="same")) == ["same-000.png"]


# -- sections ------------------------------------------------------------------------------------------------------

DEE, DEE_Q, ELI = ("Dee", "dee@example.test"), ("Dee Q.", "DEE@example.test"), ("Eli", "eli@example.test")


def two_senders():
    """Dee's newest file is newer than any of Eli's, and Dee's other, under another name and in capitals, is older than all of them,
    so a section must hold files the newest-first order would split."""
    return [doc(1, "dee-000.pdf", DEE), doc(2, "dee-001.pdf", DEE),
            doc(3, "eli-000.png", ELI, "image/png"), doc(4, "eli-001.png", ELI, "image/png"),
            doc(5, "dee-old-000.pdf", DEE_Q)]


def test_with_no_section_asked_for_there_are_none():
    answer = page(two_senders())
    assert answer["groups"] == [] and {d["group"] for d in answer["docs"]} == {""}


def test_by_sender_each_section_holds_all_of_that_senders_files_the_newest_senders_first():
    answer = page(two_senders(), group="sender")
    assert names(answer) == ["dee-000.pdf", "dee-001.pdf", "dee-old-000.pdf", "eli-000.png", "eli-001.png"]
    assert answer["groups"] == [{"id": "dee@example.test", "name": "Dee", "count": 3},        # as the newest calls them
                                {"id": "eli@example.test", "name": "Eli", "count": 2}]
    assert [d["group"] for d in answer["docs"]] == ["dee@example.test"] * 3 + ["eli@example.test"] * 2


def test_by_sender_whoever_sent_the_newest_file_comes_first():
    eli_first = [doc(0, "eli-new.png", ELI, "image/png"), *two_senders()]
    assert [g["id"] for g in page(eli_first, group="sender")["groups"]] == ["eli@example.test", "dee@example.test"]


def test_by_type_the_sections_go_in_the_order_of_the_kinds_whatever_is_newest():
    rows = [doc(0, "eli-new.png", ELI, "image/png"), *two_senders()]
    answer = page(rows, group="type")
    order = [k for k in get_args(documents.DocumentKind.__value__) if k in answer["facets"]]
    assert [g["id"] for g in answer["groups"]] == order == ["pdf", "image"]
    assert [d["kind"] for d in answer["docs"]] == ["pdf"] * 3 + ["image"] * 3
    assert all(d["group"] == d["kind"] for d in answer["docs"])
    assert sum(g["count"] for g in answer["groups"]) == answer["matched"]


def test_pages_in_sections_cover_every_file_once_in_the_order_of_the_whole():
    whole = page(two_senders(), group="sender")
    seen = []
    for offset in range(0, whole["matched"] + 3, 2):
        part = page(two_senders(), group="sender", limit="2", offset=str(offset))
        assert part["groups"] == whole["groups"]            # the whole list's sections, whichever page this is
        seen += part["docs"]
    assert seen == whole["docs"]


def test_sections_count_only_what_the_search_and_the_kind_left():
    images = page(two_senders(), group="sender", kind="image")
    assert images["groups"] == [{"id": "eli@example.test", "name": "Eli", "count": 2}]
    assert images["facets"] == page(two_senders())["facets"]                       # what the chips say doesn't move
    old = page(two_senders(), group="sender", q="old")
    assert old["groups"] == [{"id": "dee@example.test", "name": "Dee Q.", "count": 1}]


def test_a_section_nobody_sent_is_one_with_no_name():
    rows = [*two_senders(), doc(6, "nobody-000.pdf", (None, None))]
    assert [g for g in page(rows, group="sender")["groups"] if g["id"] == ""] == [{"id": "", "name": "", "count": 1}]
