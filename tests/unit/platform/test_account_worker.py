"""mailmind.platform.workers.account_worker's kept connections, with plain objects for connections.
test_gates.py holds the gate through its façade, and tests/unit/mailbox/test_pool.py the keeping as
the Mailbox binds it."""
import ast
import pathlib
import sys

import pytest

from mailmind.platform.workers import account_worker
from mailmind.platform.workers.account_worker import ConnectionPolicy, KeptConnections

LONG = ConnectionPolicy(max_age=1000, max_uses=1000)


class Kept:
    """KeptConnections over objects, writing down what it opens, checks and closes."""

    def __init__(self):
        self.opened, self.checked, self.closed = [], [], []
        self.answers = True
        self.kept = KeptConnections(self.check, self.closed.append)

    def check(self, conn):
        self.checked.append(conn)
        return self.answers

    def open_new(self):
        self.opened.append(object())
        return self.opened[-1]

    def borrow(self, key="k", policy=LONG):
        with self.kept.lend(key, policy, self.open_new) as conn:
            return conn


@pytest.fixture
def k():
    return Kept()


@pytest.mark.parametrize("age, uses, spent", [(0, 0, False), (90, 39, False), (90.5, 39, True), (0, 40, True), (91, 41, True)])
def test_a_connection_is_spent_past_its_age_or_at_its_uses(age, uses, spent):
    assert ConnectionPolicy(max_age=90, max_uses=40).spent(age, uses) is spent


def test_the_first_borrow_opens_and_the_next_reuses_after_a_check(k):
    first = k.borrow()
    assert k.checked == [] and k.borrow() is first and k.checked == [first] and k.closed == []
    assert k.kept.counters() == {"created": 1, "reused": 1, "validate_fail": 0, "discard_dirty": 0}


def test_a_connection_is_spent_without_a_check(k):
    first = k.borrow(policy=ConnectionPolicy(max_age=1000, max_uses=1))
    assert k.borrow(policy=ConnectionPolicy(max_age=1000, max_uses=1)) is not first
    assert k.checked == [] and k.closed == [first]
    assert k.kept.counters()["validate_fail"] == 0


def test_a_connection_that_fails_its_check_is_closed_and_replaced(k):
    first = k.borrow()
    k.answers = False
    assert k.borrow() is not first and k.closed == [first]
    assert k.kept.counters()["validate_fail"] == 1


def test_a_borrow_that_raises_closes_the_connection_and_lets_the_error_through(k):
    first = k.borrow()
    with pytest.raises(KeyboardInterrupt):                     # any error, even this
        with k.kept.lend("k", LONG, k.open_new):
            raise KeyboardInterrupt
    assert k.closed == [first] and k.kept.slots()["k"]["open"] is False
    assert k.kept.counters()["discard_dirty"] == 1
    assert k.borrow() is not first and k.checked == [first]    # the check of the borrow that raised, and none since


def test_a_connection_that_wont_open_leaves_the_slot_empty(k):
    def refuses():
        raise ConnectionRefusedError("no one there")
    with pytest.raises(ConnectionRefusedError):
        with k.kept.lend("k", LONG, refuses):
            pass
    assert k.kept.slots()["k"]["open"] is False and k.kept.counters()["created"] == 0
    k.borrow()
    assert k.opened and k.kept.slots()["k"]["open"] is True


def test_each_key_keeps_its_own(k):
    assert k.borrow("a") is not k.borrow("b")
    assert set(k.kept.slots()) == {"a", "b"} and k.checked == []


def test_close_all_closes_what_is_kept_once(k):
    a, b = k.borrow("a"), k.borrow("b")
    k.kept.close_all()
    k.kept.close_all()
    assert k.closed == [a, b]
    assert k.borrow("a") not in (a, b)


def test_it_asks_only_the_standard_library_and_knows_nothing_of_mail():
    source = pathlib.Path(account_worker.__file__).read_text(encoding="utf-8")
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module.split(".")[0])
    assert names <= sys.stdlib_module_names
    assert not {"imap", "imaplib", "noop", "logout", "proton", "bridge"} & {w.strip(".,:;()`'\"").lower() for w in source.split()}
