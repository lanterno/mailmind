"""Saved views on disk: what a load skips, and how they are written."""
import json
import os

from mailmind.discovery.domain import views as builtin


def test_hwf_views_load_skips_malformed_entries_and_builtin_ids(data_dir):
    from mailmind.bootstrap.discovery import views
    with open(data_dir / "mailmind_views.json", "w") as f:
        json.dump([{"id": "v1", "name": "From Lin", "q": "from:lin"}, {"id": "__unread__", "name": "Spoof", "q": "x"},
                   {"id": "v2", "name": " ", "q": "x"}, "junk", {"id": "v3", "name": "N" * 80, "q": "q" * 600}], f)
    assert views.load() == [{"id": "v1", "name": "From Lin", "q": "from:lin"},
                            {"id": "v3", "name": "N" * 60, "q": "q" * 512}]
    merged = views.merged()
    assert [v["id"] for v in merged if v["builtin"]] == [v["id"] for v in builtin.DEFAULTS + builtin.LANES]
    assert views.remove("__unread__") is False


def test_hwf_saved_views_are_written_owner_only_and_without_builtins(data_dir):
    from mailmind.bootstrap.discovery import views
    views.add("From Lin", "from:lin")
    assert os.stat(data_dir / "mailmind_views.json").st_mode & 0o777 == 0o600
    with open(data_dir / "mailmind_views.json") as f:
        assert [v["name"] for v in json.load(f)] == ["From Lin"]
