"""The Document Hub's list: the server pages it and filters it, so a search reaches every file."""
from typing import get_args

import pytest

import problems
from fixtures import world as w
from mailmind.discovery.domain import documents

NEWS = "ada-inbox-03-newsletter.eml"
LUNCH = "ada-inbox-02-lunch-confirm.eml"


def _files(world, name, n, stem="report", ext="pdf", ctype="application/pdf"):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        for i in range(n):
            c.execute("INSERT INTO attachments(message_pk, part_id, filename, content_type, size, is_inline) "
                      "VALUES (?,?,?,?,?,0)", (world.pk(name), f"{stem}.{i}", f"{stem}-{i:03}.{ext}", ctype, 1000 + i))
        c.commit()
    finally:
        c.close()


def _docs(app, query):
    return app.get(f"/api/documents?{query}").json


def test_pages_cover_every_file_once(app, world):
    _files(world, NEWS, 25)
    base = _docs(app, "limit=1000")
    assert base["matched"] == base["total"] >= 25
    seen = []
    for offset in range(0, base["total"] + 10, 10):
        page = _docs(app, f"limit=10&offset={offset}")
        assert page["matched"] == base["total"]
        assert page["shown"] == len(page["docs"]) <= 10
        seen += page["docs"]
    assert seen == base["docs"]


def test_past_the_end_is_an_empty_page(app, world):
    _files(world, NEWS, 3)
    page = _docs(app, "offset=100000")
    assert page["docs"] == [] and page["shown"] == 0 and page["matched"] == page["total"] >= 3


@pytest.mark.parametrize("bad", ["nope", "-5", ""])
def test_an_offset_that_makes_no_sense_means_the_start(app, world, bad):
    _files(world, NEWS, 3)
    assert _docs(app, f"offset={bad}")["docs"] == _docs(app, "")["docs"]


def test_a_search_reaches_past_the_first_page(app, world):
    _files(world, NEWS, 30)
    _files(world, LUNCH, 1, stem="needle")
    assert "needle-000.pdf" not in [d["filename"] for d in _docs(app, "limit=5")["docs"]]
    found = _docs(app, "limit=5&q=NEEDLE")
    assert [d["filename"] for d in found["docs"]] == ["needle-000.pdf"]
    assert found["matched"] == 1


def test_a_search_reads_the_sender_and_the_subject_too(app, world):
    _files(world, LUNCH, 1, stem="plain")
    r = world.row(LUNCH)
    for q in (r["from_addr"], r["subject"][:6]):
        assert "plain-000.pdf" in [d["filename"] for d in _docs(app, f"q={q.replace(' ', '%20')}")["docs"]], q


def test_a_search_that_finds_nothing_says_so_and_keeps_the_counts(app, world):
    _files(world, NEWS, 3)
    every = _docs(app, "")
    found = _docs(app, "q=nothing-is-called-this")
    assert (found["docs"], found["matched"]) == ([], 0)
    assert (found["total"], found["facets"]) == (every["total"], every["facets"])


def test_a_kind_gives_that_kind_and_pages_within_it(app, world):
    _files(world, NEWS, 12)
    _files(world, LUNCH, 12, stem="photo", ext="png", ctype="image/png")
    every = _docs(app, "limit=1000")
    images = _docs(app, "kind=image&limit=5")
    assert images["matched"] == every["facets"]["image"]
    assert {d["kind"] for d in images["docs"]} == {"image"} and images["shown"] == 5
    assert images["facets"] == every["facets"]
    assert _docs(app, "kind=image&limit=5&offset=5")["docs"][0] not in images["docs"]


def test_a_kind_and_a_search_both_hold(app, world):
    _files(world, NEWS, 2, stem="same")
    _files(world, LUNCH, 2, stem="same", ext="png", ctype="image/png")
    found = _docs(app, "kind=image&q=same")
    assert sorted(d["filename"] for d in found["docs"]) == ["same-000.png", "same-001.png"]


def test_the_same_file_in_two_places_is_counted_once_across_pages(app, world):
    world.deliver(w.ADA, "Archive", world.server.messages(w.ADA, "INBOX")[world.row(NEWS)["uid"]].raw)
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        pks = [r["pk"] for r in c.execute("SELECT pk FROM messages WHERE message_id=?", (world.row(NEWS)["message_id"],))]
    finally:
        c.close()
    assert len(pks) == 2
    c = store.conn()
    try:
        for pk in pks:
            c.execute("INSERT INTO attachments(message_pk, part_id, filename, content_type, size, is_inline) "
                      "VALUES (?,'2','twin.pdf','application/pdf',777,0)", (pk,))
        c.commit()
    finally:
        c.close()
    found = _docs(app, "q=twin")
    assert found["matched"] == 1 and len(found["docs"]) == 1


def _from(world, name, sender, addr):
    """The sender of a message's row, set to a fictional one."""
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        c.execute("UPDATE messages SET from_name=?, from_addr=? WHERE pk=?", (sender, addr, world.pk(name)))
        c.commit()
    finally:
        c.close()


def _ts(world, name, ts):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        c.execute("UPDATE messages SET ts=? WHERE pk=?", (ts, world.pk(name)))
        c.commit()
    finally:
        c.close()


OLDEST, MIDDLE, NEWEST = 1_700_000_000, 1_750_000_000, 1_790_000_000
THIRD = "ada-inbox-04-ci.eml"


@pytest.fixture
def two_senders(world):
    """Dee's newest file is newer than any of Eli's, and Dee's other is older than all of them, so a section must hold files the
    newest-first order would split."""
    _files(world, NEWS, 2, stem="dee")
    _files(world, LUNCH, 2, stem="eli", ext="png", ctype="image/png")
    _files(world, THIRD, 1, stem="dee-old")
    _from(world, NEWS, "Dee", "dee@example.test")
    _from(world, LUNCH, "Eli", "eli@example.test")
    _from(world, THIRD, "Dee Q.", "DEE@example.test")          # the same person, by another name and in capitals
    _ts(world, NEWS, NEWEST), _ts(world, LUNCH, MIDDLE), _ts(world, THIRD, OLDEST)


def _ours(docs):
    return [d["filename"] for d in docs if d["filename"].startswith(("dee", "eli"))]


def test_with_no_section_asked_for_there_are_none(app, world, two_senders):
    found = _docs(app, "limit=1000")
    assert found["groups"] == [] and {d["group"] for d in found["docs"]} == {""}


def test_by_sender_each_section_holds_all_of_that_senders_files_the_newest_senders_first(app, world, two_senders):
    found = _docs(app, "group=sender&limit=1000&q=example.test")
    assert _ours(found["docs"]) == ["dee-000.pdf", "dee-001.pdf", "dee-old-000.pdf", "eli-000.png", "eli-001.png"]
    named = {g["id"]: g for g in found["groups"]}
    assert named["dee@example.test"] == {"id": "dee@example.test", "name": "Dee", "count": 3}   # as the newest calls them
    assert named["eli@example.test"]["count"] == 2
    ids = [g["id"] for g in found["groups"]]
    assert ids.index("dee@example.test") < ids.index("eli@example.test")
    assert {d["group"] for d in found["docs"] if d["filename"].startswith("dee")} == {"dee@example.test"}


def test_by_sender_whoever_sent_the_newest_file_comes_first_whatever_they_are_called(app, world, two_senders):
    _ts(world, LUNCH, NEWEST + 1)
    ids = [g["id"] for g in _docs(app, "group=sender&limit=1000")["groups"]]
    assert ids.index("eli@example.test") < ids.index("dee@example.test")


def test_by_type_the_sections_go_in_the_order_of_the_kinds(app, world, two_senders):
    _ts(world, LUNCH, NEWEST + 1)                              # an image is the newest file, and PDFs still come first
    found = _docs(app, "group=type&limit=1000")
    assert found["docs"] and [d["kind"] for d in _docs(app, "limit=1")["docs"]] == ["image"]
    assert [g["id"] for g in found["groups"]] == [k for k in get_args(documents.DocumentKind.__value__) if k in found["facets"]]
    kinds = [d["kind"] for d in found["docs"]]
    assert kinds == sorted(kinds, key=[g["id"] for g in found["groups"]].index)
    assert all(d["group"] == d["kind"] for d in found["docs"])
    assert sum(g["count"] for g in found["groups"]) == found["matched"]


def test_pages_in_sections_cover_every_file_once_in_the_order_of_the_whole(app, world, two_senders):
    whole = _docs(app, "group=sender&limit=1000")
    seen = []
    for offset in range(0, whole["matched"] + 3, 3):
        page = _docs(app, f"group=sender&limit=3&offset={offset}")
        assert page["groups"] == whole["groups"]            # the whole list's sections, whichever page this is
        seen += page["docs"]
    assert seen == whole["docs"]


def test_sections_count_only_what_the_search_and_the_kind_left(app, world, two_senders):
    found = _docs(app, "group=sender&kind=image&limit=1000")
    assert {g["id"]: g["count"] for g in found["groups"]}["eli@example.test"] == 2
    assert "dee@example.test" not in {g["id"] for g in found["groups"]}
    assert found["facets"] == _docs(app, "limit=1000")["facets"]                # what the chips say doesn't move


def test_a_section_nobody_sent_is_one_with_no_name(app, world, two_senders):
    _from(world, THIRD, None, None)
    found = _docs(app, "group=sender&limit=1000")
    assert [g for g in found["groups"] if g["id"] == ""] == [{"id": "", "name": "", "count": 1}]


@pytest.mark.parametrize("bad", ["nope", "SENDER"])
def test_a_grouping_the_server_doesnt_have_is_the_bad_request(app, bad):
    r = app.get(f"/api/documents?group={bad}")
    assert (r.status, r.json) == (400, problems.BAD_REQUEST)
