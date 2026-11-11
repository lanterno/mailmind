"""mailmind_views.json through mailmind.discovery.adapters.views_file: a read never raises, and a write is atomic and
readable by its owner only, or leaves the file as it was."""
import json
import os
import stat

import pytest

from mailmind.discovery.adapters import views_file

VIEWS = [{"id": "u1", "name": "Receipts", "query": "from:shop.example.test"}]


def path(data_dir):
    return data_dir / "mailmind_views.json"


def leftovers(data_dir):
    return [p.name for p in data_dir.iterdir() if p.name.startswith(".views-")]


def test_no_file_reads_as_no_views(data_dir):
    assert views_file.read() == []


@pytest.mark.parametrize("held", ["", "{not json", "ÿþ"], ids=["empty", "torn", "not-text"])
def test_a_file_that_wont_read_reads_as_no_views(data_dir, held):
    path(data_dir).write_text(held, encoding="latin-1")
    assert views_file.read() == []


def test_a_write_is_read_back_and_is_its_owners_only(data_dir):
    views_file.write(VIEWS)
    assert views_file.read() == VIEWS
    assert stat.S_IMODE(os.stat(path(data_dir)).st_mode) == 0o600
    assert leftovers(data_dir) == []


def test_a_write_keeps_what_isnt_ascii_as_written(data_dir):
    views_file.write([{"id": "u2", "name": "Café", "query": "from:café.example.test"}])
    assert "Café" in path(data_dir).read_text(encoding="utf-8")


def test_a_write_that_fails_on_its_way_leaves_the_file_and_no_temporary_one(data_dir, monkeypatch):
    views_file.write(VIEWS)

    def full(src, dst):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(os, "replace", full)
    with pytest.raises(OSError, match="No space left"):
        views_file.write([])
    monkeypatch.undo()
    assert json.loads(path(data_dir).read_text()) == VIEWS
    assert leftovers(data_dir) == []


def test_what_wont_go_in_a_file_is_refused_before_one_is_touched(data_dir):
    views_file.write(VIEWS)
    with pytest.raises(TypeError):
        views_file.write([{"id": "u3", "name": {"a", "set"}}])
    assert views_file.read() == VIEWS and leftovers(data_dir) == []
