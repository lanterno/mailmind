"""The saved views as the database keeps them, through mailmind.discovery.adapters.views_store and SavedViews: what a load
gives when nothing, or nothing good, is kept, what a save leaves, and that two saves at once lose neither."""
import json

import pytest

from at_once import HeldAfterItsRead, overlap
from mailmind.discovery.adapters import views_store
from mailmind.discovery.application.views import SavedViews
from mailmind.platform.db.uow import UnitOfWork

LIN = {"id": "v1", "name": "From Lin", "q": "from:lin"}


def kept():
    """The views document as the database holds it: its JSON parsed, and its version."""
    with UnitOfWork().read() as c:
        row = c.execute("SELECT body, version FROM documents WHERE name='views'").fetchone()
    return None if row is None else (json.loads(row["body"]), row["version"])


def put(text):
    """`text` as the views document, as an old mailmind_views.json goes in."""
    with UnitOfWork().write() as c:
        c.execute("INSERT INTO documents(name, body, changed_at) VALUES('views', ?, '2026-11-11T00:00:00+00:00')", (text,))


def saved_views(store=views_store):
    return SavedViews(UnitOfWork(), store)


def test_none_kept_reads_as_no_views_and_a_load_keeps_nothing(data_dir):
    assert saved_views().load() == []
    assert kept() is None


@pytest.mark.parametrize("text", ["", "{not json", '{"id": "v1"}'], ids=["empty", "torn", "not-a-list"])
def test_what_wont_read_as_views_reads_as_none(data_dir, text):
    put(text)
    assert saved_views().load() == []


def test_a_save_keeps_the_views_alone_in_the_shape_the_file_held(data_dir):
    made = saved_views().add("Café", "from:café.example.test")
    assert made is not None and set(made) == {"id", "name", "q"}
    assert kept() == ([made], 1)


def test_an_old_files_text_reads_as_its_views_and_a_save_adds_to_them(data_dir):
    put(json.dumps([LIN], indent=2))
    use = saved_views()
    use.add("Receipts", "is:receipt")
    assert [v["name"] for v in use.load()] == ["From Lin", "Receipts"]
    assert kept()[0][0] == LIN


def test_a_view_removed_is_gone_and_what_changes_nothing_writes_nothing(data_dir):
    put(json.dumps([LIN]))
    use = saved_views()
    assert use.remove("__unread__") is False and use.remove("v404") is False
    assert kept() == ([LIN], 1)
    assert use.remove("v1") is True
    assert kept() == ([], 2) and use.load() == []


def test_a_save_that_fails_on_its_way_leaves_what_was_kept(data_dir):
    put(json.dumps([LIN]))

    class Full:
        read = staticmethod(views_store.read)

        @staticmethod
        def write(c, user_views):
            views_store.write(c, user_views)
            raise OSError(28, "No space left on device")

    with pytest.raises(OSError, match="No space left"):
        saved_views(Full).add("Receipts", "is:receipt")
    assert kept() == ([LIN], 1)


def test_two_saves_at_once_lose_neither(data_dir):
    """Two SavedViews, as the page's and the command line's would be, each saving a view while the other does."""
    held = HeldAfterItsRead(views_store)
    page, cli = saved_views(held), saved_views(held)
    overlap(held, lambda: page.add("From Lin", "from:lin"), lambda: cli.add("Receipts", "is:receipt"))
    assert sorted(v["name"] for v in saved_views().load()) == ["From Lin", "Receipts"]


def test_two_removes_at_once_lose_neither(data_dir):
    put(json.dumps([LIN, {"id": "v2", "name": "Receipts", "q": "is:receipt"}, {"id": "v3", "name": "Kept", "q": "is:flagged"}]))
    held = HeldAfterItsRead(views_store)
    page, cli = saved_views(held), saved_views(held)
    overlap(held, lambda: page.remove("v1"), lambda: cli.remove("v2"))
    assert [v["name"] for v in saved_views().load()] == ["Kept"]
