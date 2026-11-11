"""A change reads the rule book, changes it and writes the whole of it back, and the routes run on threads of their own. Two at
once each keep what they came for, and neither loses the other's. The command line and the server are two processes, so it holds
across those too: each change is one write transaction, which takes the database's write lock before it reads."""
import os
import pathlib
import subprocess
import sys
import threading

import pytest

from mailmind.bootstrap.curation import rules
from mailmind.curation.domain import rulebook
from mailmind.platform import settings
from mailmind.platform.db import database
from stored_rows import document

KEPT = "kept.example.test"
GONE = "gone.example.test"
ALSO = "also.example.test"
SHELF = "shelf.example.test"


def add():
    rules.add(KEPT, "keep")


def add_too():
    rules.add(ALSO, "block")


def remove():
    rules.remove(GONE)


def set_expiry():
    rules.set_expiry(SHELF, 72)


def did(change, r):
    return {add: KEPT in r["keep_domains"], add_too: ALSO in r["block_domains"], remove: GONE not in r["block_domains"],
            set_expiry: r[rulebook.EXPIRE].get(SHELF) == 72}[change]


@pytest.mark.parametrize("first, second", [(add, add_too), (add, remove), (add, set_expiry), (remove, set_expiry)],
                         ids=lambda f: f.__name__)
def test_two_changes_at_once_both_stand(data_dir, monkeypatch, first, second):
    """Each change waits, once it has read, for the other to have read too. Unless only one may read at a time, both start from
    the same rules. When one may, the other never arrives, the wait gives up and the second reads what the first wrote."""
    rules.add(GONE, "block")
    read = rules._held
    both_read = threading.Barrier(2)

    def read_and_wait(c):
        got = read(c)
        try:
            both_read.wait(timeout=0.3)
        except threading.BrokenBarrierError:
            pass
        return got

    monkeypatch.setattr(rules, "_held", read_and_wait)
    threads = [threading.Thread(target=change) for change in (first, second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    monkeypatch.undo()
    assert did(first, rules.load()) and did(second, rules.load())


def test_a_change_waits_for_whoever_holds_the_write_lock(data_dir):
    """As another process would: a connection of its own, holding the database's write lock."""
    held = database.connect(settings.current().db)
    try:
        held.execute("BEGIN IMMEDIATE")
        changing = threading.Thread(target=add, daemon=True)
        changing.start()
        changing.join(0.3)
        assert changing.is_alive() and document("rules") is None
    finally:
        held.rollback()
        held.close()
    changing.join(10)
    assert not changing.is_alive() and did(add, rules.load())


def test_two_processes_changing_the_rules_lose_nothing(data_dir):
    """The server and `python -m mailmind rules` at once: every rule either added is there at the end."""
    each, root = 25, pathlib.Path(__file__).resolve().parents[3]
    code = ("import sys; from mailmind.curation.adapters import rules_document as rules; "
            f"[rules.add(f'{{sys.argv[1]}}-{{i}}.example.test', 'keep') for i in range({each})]")
    env = {**os.environ, "MAILMIND_HOME": str(data_dir)}
    running = [subprocess.Popen([sys.executable, "-c", code, name], cwd=root, env=env) for name in ("cli", "server", "other")]
    assert [p.wait(60) for p in running] == [0, 0, 0]
    kept = rules.load()["keep_domains"]
    assert len(kept) == 3 * each and {f"{n}-{i}.example.test" for n in ("cli", "server", "other") for i in range(each)} == set(kept)
