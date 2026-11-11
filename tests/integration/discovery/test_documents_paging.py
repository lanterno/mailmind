"""The Document Hub's list through its route, over the store: pages asked one request at a time cover every file once, since the
store gives them in one order, a file two copies hold is one, the store's dates say whose section comes first, and a grouping
there isn't is refused. What a page comes to from the rows is tests/unit/discovery/test_documents_paging.py."""
import pytest

import problems
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


def test_by_sender_whoever_sent_the_newest_file_comes_first_whatever_they_are_called(app, world, two_senders):
    _ts(world, LUNCH, NEWEST + 1)
    ids = [g["id"] for g in _docs(app, "group=sender&limit=1000")["groups"]]
    assert ids.index("eli@example.test") < ids.index("dee@example.test")


@pytest.mark.parametrize("bad", ["nope", "SENDER"])
def test_a_grouping_the_server_doesnt_have_is_the_bad_request(app, bad):
    r = app.get(f"/api/documents?group={bad}")
    assert (r.status, r.json) == (400, problems.BAD_REQUEST)
