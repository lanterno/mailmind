"""The rule book in the database (mailmind.curation.adapters.rules_document): the old file's text is what it holds, nothing reads
or writes the file, a read parses it again only when its version moved, and a write that fails leaves it as it was."""
import json
import os

import pytest

from mailmind.bootstrap import curation
from mailmind.bootstrap.curation import rules
from mailmind.curation.domain import rulebook
from mailmind.platform.db import documents
from mailmind.platform.db.uow import UnitOfWork
from stored_rows import document, put_document, q

# A rules file as a person may have kept it: the example's note, any case, and a filter.
OLD_FILE = """{
  "_README": "Learned allow/block lists the classifier consults FIRST.",
  "keep_senders": ["Lin@Example.test"],
  "keep_domains": [],
  "block_senders": [],
  "block_domains": ["prizes.example.test"],
  "expire_hours": {"Notify.Example.Test": 72},
  "filters": [{"id": "f-1", "q": "from:loom", "star": true, "read": false}]
}"""


def test_an_import_is_the_files_text_inserted_as_it_is(data_dir):
    """What the owner runs once, and what a change writes back: the text a file of rules held."""
    with UnitOfWork().write() as c:
        c.execute("INSERT INTO documents(name, body, changed_at) VALUES('rules', ?, ?)", (OLD_FILE, "2026-11-02T12:00:00+00:00"))
    assert rules.load() == {"keep_senders": ["lin@example.test"], "block_senders": [], "keep_domains": [],
                            "block_domains": ["prizes.example.test"], "expire_hours": {"notify.example.test": 72},
                            "filters": [{"id": "f-1", "q": "from:loom", "star": True, "read": False}]}
    assert curation.classify("lin@example.test", "Win a prize now")[2] == "kept by rule (sender)"
    rules.add("kept.example.test", "keep")
    assert document("rules") == json.dumps({"keep_senders": ["lin@example.test"], "block_senders": [],
                                            "keep_domains": ["kept.example.test"], "block_domains": ["prizes.example.test"],
                                            "expire_hours": {"notify.example.test": 72},
                                            "filters": [{"id": "f-1", "q": "from:loom", "star": True, "read": False}]}, indent=2)


def test_a_rules_file_beside_the_database_is_read_and_written_by_nothing(data_dir):
    old = data_dir / "mailmind_rules.json"
    old.write_text(OLD_FILE)
    before = os.stat(old)
    assert rules.load() == rulebook.empty()
    assert curation.classify("lin@example.test", "Win a prize now")[2] != "kept by rule (sender)"
    rules.add("other.example.test", "block")
    assert old.read_text() == OLD_FILE and os.stat(old).st_mtime_ns == before.st_mtime_ns
    assert not (data_dir / ".rules.lock").exists() and not (data_dir / "backups").exists()


def test_a_read_parses_the_rule_book_again_only_when_its_version_moved(data_dir, monkeypatch):
    put_document("rules", json.dumps({"keep_senders": ["lin@example.test"]}))
    parse, parsed = rulebook.normalized, []

    def counted(raw):
        parsed.append(raw)
        return parse(raw)
    monkeypatch.setattr(rulebook, "normalized", counted)
    for _ in range(3):
        assert rules.sets()["keep_senders"] == {"lin@example.test"}
        assert rules.load()["keep_senders"] == ["lin@example.test"]
    assert len(parsed) == 1
    put_document("rules", json.dumps({"keep_senders": ["ada@example.test"]}))
    assert rules.sets()["keep_senders"] == {"ada@example.test"} and len(parsed) == 2


def test_a_write_that_fails_keeps_the_rules(data_dir, monkeypatch):
    rules.add("lin@example.test", "keep")
    write = documents.write

    def cut_short(c, name, body):
        write(c, name, body)
        raise OSError("disk I/O error")
    monkeypatch.setattr(documents, "write", cut_short)
    with pytest.raises(OSError):
        rules.add("prizes.example.test", "block")
    monkeypatch.undo()
    assert rules.load()["block_domains"] == [] and rules.load()["keep_senders"] == ["lin@example.test"]
    assert q("SELECT version FROM documents WHERE name='rules'") == [{"version": 1}]
