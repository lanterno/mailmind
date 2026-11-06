"""The Document Hub's list: the server pages it and filters it, so a search reaches every file."""
import pytest

from fixtures import world as w

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
