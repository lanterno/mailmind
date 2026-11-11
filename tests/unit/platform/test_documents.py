"""A document kept whole, as a row of `documents`: what a read gives, what a write leaves, and that its body is the text the
file it was kept in held, so that file's text goes in as it is."""
import json

import pytest

from mailmind.platform.db import documents
from mailmind.platform.db.uow import UnitOfWork

VIEWS = [{"id": "v1", "name": "Café", "q": "from:café.example.test"}]


def row(name):
    with UnitOfWork().read() as c:
        r = c.execute("SELECT body, version, changed_at FROM documents WHERE name=?", (name,)).fetchone()
        return None if r is None else dict(r)


def keep(name, value):
    with UnitOfWork().write() as c:
        documents.write(c, name, value)


def held(name):
    with UnitOfWork().read() as c:
        return documents.read(c, name)


def test_none_kept_reads_as_none(data_dir):
    assert held("views") is None


def test_a_write_is_read_back_as_the_text_a_file_of_it_held(data_dir):
    keep("views", VIEWS)
    assert held("views") == VIEWS
    assert row("views")["body"] == json.dumps(VIEWS, indent=2, ensure_ascii=False)        # as a person reads it, Café and all


def test_a_files_text_put_in_as_it_is_reads_as_the_file_did(data_dir):
    text = '[\n  {"trigger": "sig", "text": "Thanks,\\nAda"}\n]\n'
    with UnitOfWork().write() as c:
        c.execute("INSERT INTO documents(name, body, changed_at) VALUES('snippets', ?, '2026-11-11T00:00:00+00:00')", (text,))
    assert held("snippets") == [{"trigger": "sig", "text": "Thanks,\nAda"}]


@pytest.mark.parametrize("body", ["", "{not json", "[1,"], ids=["empty", "torn", "cut-short"])
def test_a_body_that_wont_parse_reads_as_none(data_dir, body):
    with UnitOfWork().write() as c:
        c.execute("INSERT INTO documents(name, body, changed_at) VALUES('views', ?, '2026-11-11T00:00:00+00:00')", (body,))
    assert held("views") is None


def test_each_save_is_a_version_on_and_says_when(data_dir):
    keep("views", [])
    first = row("views")
    keep("views", VIEWS)
    second = row("views")
    assert (first["version"], second["version"]) == (1, 2)
    assert first["changed_at"] and second["changed_at"] >= first["changed_at"]
    assert held("views") == VIEWS


def test_each_name_is_a_document_of_its_own(data_dir):
    keep("views", VIEWS)
    keep("snippets", [{"trigger": "sig", "text": "Ada"}])
    assert held("views") == VIEWS and held("snippets") == [{"trigger": "sig", "text": "Ada"}]
    assert row("views")["version"] == 1


def test_what_json_cant_hold_is_refused_and_leaves_what_was_kept(data_dir):
    keep("views", VIEWS)
    with pytest.raises(TypeError):
        keep("views", [{"id": "v2", "name": {"a", "set"}}])
    assert held("views") == VIEWS and row("views")["version"] == 1


def test_a_write_is_its_callers_to_commit(data_dir):
    with pytest.raises(RuntimeError):
        with UnitOfWork().write() as c:
            documents.write(c, "views", VIEWS)
            raise RuntimeError("the change went no further")
    assert held("views") is None


def test_a_reader_that_keeps_one_asks_its_version_and_can_have_the_text_that_wont_parse(data_dir):
    with UnitOfWork().read() as c:
        assert documents.version(c, "rules") is None and documents.stored(c, "rules") is None
    keep("rules", {"keep_senders": []})
    keep("rules", {"keep_senders": ["lin@example.test"]})
    with UnitOfWork().write() as c:
        assert documents.version(c, "rules") == 2
        c.execute("UPDATE documents SET body='{\"keep_senders\": [' WHERE name='rules'")
        assert documents.stored(c, "rules") == ('{"keep_senders": [', 2)     # the rule book raises on it, never empties
        assert documents.read(c, "rules") is None
