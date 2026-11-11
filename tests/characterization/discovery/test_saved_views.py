"""Saved views as the database keeps them: what a load skips, and what a save keeps."""
import json

from mailmind.discovery.domain import views as builtin
from mailmind.platform.db.uow import UnitOfWork


def test_hwf_views_load_skips_malformed_entries_and_builtin_ids(data_dir):
    from mailmind.bootstrap.discovery import views
    held = [{"id": "v1", "name": "From Lin", "q": "from:lin"}, {"id": "__unread__", "name": "Spoof", "q": "x"},
            {"id": "v2", "name": " ", "q": "x"}, "junk", {"id": "v3", "name": "N" * 80, "q": "q" * 600}]
    with UnitOfWork().write() as c:
        c.execute("INSERT INTO documents(name, body, changed_at) VALUES('views', ?, '2026-11-11T00:00:00+00:00')", (json.dumps(held),))
    assert views.load() == [{"id": "v1", "name": "From Lin", "q": "from:lin"},
                            {"id": "v3", "name": "N" * 60, "q": "q" * 512}]
    merged = views.merged()
    assert [v["id"] for v in merged if v["builtin"]] == [v["id"] for v in builtin.DEFAULTS + builtin.LANES]
    assert views.remove("__unread__") is False


def test_hwf_saved_views_are_kept_in_the_database_without_builtins(data_dir):
    from mailmind.bootstrap.discovery import views
    views.add("From Lin", "from:lin")
    with UnitOfWork().read() as c:
        body = c.execute("SELECT body FROM documents WHERE name='views'").fetchone()["body"]
    assert [v["name"] for v in json.loads(body)] == ["From Lin"]
    assert not (data_dir / "mailmind_views.json").exists()
