"""A change reads the rules, changes them and writes the whole file back, and the routes run on threads of their own. Two at
once each keep what they came for, and neither loses the other's."""
import threading

import pytest

from mailmind.bootstrap.curation import rules
from mailmind.curation.domain import rulebook

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
    read = rules.load
    both_read = threading.Barrier(2)

    def load_and_wait():
        got = read()
        try:
            both_read.wait(timeout=0.3)
        except threading.BrokenBarrierError:
            pass
        return got

    monkeypatch.setattr(rules, "load", load_and_wait)
    threads = [threading.Thread(target=change) for change in (first, second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    monkeypatch.undo()
    assert did(first, rules.load()) and did(second, rules.load())
