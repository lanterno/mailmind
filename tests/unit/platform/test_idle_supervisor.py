"""mailmind.platform.workers.idle_supervisor: one worker thread per key, kept in line with what is
wanted. Plain threads and a connection that is only an event; tests/unit/mailbox/test_idle_supervisor.py
holds the same through IDLE itself. What a worker does shows on its connection, which the subclass makes."""
import ast
import pathlib
import sys
import threading

import pytest

from mailmind.platform.workers import idle_supervisor
from mailmind.platform.workers.idle_supervisor import IdleSupervisor
from waiting import wait_until

ME = threading.current_thread().name
TICK = 2 ** -10         # a rest of moments, where the app's first is 5 seconds


class Line:
    """A connection, which knows the worker that opened it. A worker waits on it until news comes, or until it is interrupted,
    which makes the wait raise, as a socket shut down from another thread does."""

    def __init__(self, spec):
        self.spec, self.key = spec, spec[0]
        self.thread = threading.current_thread()
        self.interrupted_by = []
        self.closed_by = []
        self.news = threading.Event()
        self.shut = False
        self.deaf = False                 # it takes no notice of the first interrupt
        self.waiting = False              # its worker waits on it now

    def interrupt(self):
        self.interrupted_by.append(threading.current_thread().name)
        if self.deaf:
            self.deaf = False
            return
        self.drop()

    def drop(self):
        """The far end goes, as a server hanging up does."""
        self.shut = True
        self.news.set()

    def wait(self):
        self.waiting = True
        try:
            self.news.wait(300)
        finally:
            self.waiting = False
        self.news.clear()
        if self.shut:
            raise OSError("shut down")


class Watching(IdleSupervisor):
    """A spec is (key, way). A key is wanted the way `self.wanted` says."""

    def __init__(self, *keys):
        super().__init__()
        self.wanted = {k: "first" for k in keys}
        self.limit = 12
        self.lines = []
        self.workers = []                 # each thread that asked for a connection
        self.refuse_connect = 0           # how many connects raise
        self.never_ready = set()          # keys whose connections will never do
        self.unreadable = False
        self.serve_once = False           # a connection serves one wait, then ends
        self.said = []
        self.rests = []                   # what each failure rested for

    def _desired(self):
        return None if self.unreadable else {k: (k, way) for k, way in self.wanted.items()}

    def _same(self, running, wanted, /):
        return running == wanted

    def _wanted(self, spec, /):
        return self.wanted.get(spec[0]) == spec[1]

    def _limit(self):
        return self.limit

    def _name(self, spec, /):
        return f"watch:{spec[0]}"

    def _connect(self, spec, /):
        if threading.current_thread() not in self.workers:
            self.workers.append(threading.current_thread())
        if self.refuse_connect:
            self.refuse_connect -= 1
            raise ConnectionRefusedError("no one there")
        line = Line(spec)
        self.lines.append(line)
        return line

    def _ready(self, spec, conn, /):
        return spec[0] not in self.never_ready

    def _serve(self, watch, conn, /):
        while not watch.leaving():
            conn.wait()
            if self.serve_once or not self._wanted(watch.spec):
                break

    def _close(self, conn, /):
        conn.closed_by.append(threading.current_thread().name)

    def _say_unwanted(self, spec, /):
        self.said.append(("unwanted", spec[0]))

    def _say_failed(self, spec, error, /):
        self.said.append(("failed", spec[0], type(error).__name__))
        self.rests.append(self.next_rest(spec[0]))

    def line_of(self, key, n=0):
        return [line for line in self.lines if line.key == key][n]

    def serving(self, key):
        """Whether a worker for `key` waits on its connection, and so can be interrupted."""
        return any(line.waiting for line in self.lines if line.key == key)


@pytest.fixture
def build():
    """Build a supervisor over the keys named, and at the end stop it and see its workers have gone."""
    made = []

    def make(*keys):
        made.append(Watching(*keys))
        return made[-1]
    yield make
    for sup in made:
        sup.stop()
    threads = [t for sup in made for t in sup.workers]
    for t in threads:
        t.join(30)
    assert not any(t.is_alive() for t in threads), "a worker didn't stop"


def started(sup):
    sup.begin()
    return sup.reconcile()


def test_each_key_wanted_gets_a_worker_of_its_own(build):
    sup = build("a", "b")
    assert started(sup) == 2
    assert sup.watching() == {"a": True, "b": True}
    assert wait_until(lambda: sup.serving("a") and sup.serving("b"))
    a, b = sup.line_of("a").thread, sup.line_of("b").thread
    assert a.name == "watch:a" and a.daemon and a is not b


def test_a_second_reconcile_with_nothing_changed_leaves_the_workers_be(build):
    sup = build("a")
    started(sup)
    assert wait_until(lambda: sup.serving("a"))
    assert sup.reconcile() == 1
    assert len(sup.lines) == 1 and sup.line_of("a").interrupted_by == [] and sup.line_of("a").thread.is_alive()


def test_a_key_no_longer_wanted_is_retired_at_once(build):
    sup = build("a", "b")
    started(sup)
    assert wait_until(lambda: sup.serving("a"))
    worker = sup.line_of("a").thread
    del sup.wanted["a"]
    assert sup.reconcile() == 1
    worker.join(30)
    assert not worker.is_alive() and set(sup.watching()) == {"b"}
    assert sup.line_of("a").interrupted_by == [ME]


def test_a_key_wanted_differently_gets_a_fresh_worker(build):
    sup = build("a")
    started(sup)
    assert wait_until(lambda: sup.serving("a"))
    old = sup.line_of("a")
    sup.wanted["a"] = "second"
    sup.reconcile()
    old.thread.join(30)
    assert not old.thread.is_alive()
    assert wait_until(lambda: len(sup.lines) == 2)
    new = sup.line_of("a", 1)
    assert new.spec == ("a", "second") and new.thread is not old.thread and sup.watching() == {"a": True}


def test_workers_start_up_to_the_limit_and_a_retirement_makes_room(build):
    sup = build("a", "b", "c")
    sup.limit = 2
    assert started(sup) == 3                                   # how many are wanted, not how many run
    assert set(sup.watching()) == {"a", "b"}
    del sup.wanted["a"]
    assert sup.reconcile() == 2
    assert set(sup.watching()) == {"b", "c"}


def test_before_begin_and_after_stop_reconcile_starts_nothing(build):
    sup = build("a")
    assert sup.reconcile() == 0 and sup.watching() == {} and sup.lines == []
    started(sup)
    assert wait_until(lambda: sup.serving("a"))
    worker = sup.line_of("a").thread
    sup.stop()
    worker.join(30)
    assert sup.reconcile() == 0 and sup.watching() == {} and len(sup.lines) == 1


def test_when_what_is_wanted_cant_be_told_the_workers_stay_as_they_are(build):
    sup = build("a")
    started(sup)
    assert wait_until(lambda: sup.serving("a"))
    sup.unreadable = True
    assert sup.reconcile() == 0
    assert sup.watching() == {"a": True} and len(sup.lines) == 1 and sup.line_of("a").interrupted_by == []


def test_stop_interrupts_each_connection_and_leaves_closing_it_to_its_worker(build):
    sup = build("a", "b")
    started(sup)
    assert wait_until(lambda: sup.serving("a") and sup.serving("b"))
    a, b = sup.line_of("a"), sup.line_of("b")
    sup.stop()
    for line in (a, b):
        line.thread.join(30)
    assert a.interrupted_by == [ME] and b.interrupted_by == [ME]
    assert a.closed_by == ["watch:a"] and b.closed_by == ["watch:b"]
    assert sup.watching() == {}
    sup.stop()                                                 # each worker took its connection off the list as it went
    assert a.interrupted_by == [ME] and b.interrupted_by == [ME]


def test_a_retired_worker_still_on_its_way_out_is_interrupted_by_stop_too(build):
    """A retired worker leaves the table at once, but its connection is listed until it has gone."""
    sup = build("a")
    started(sup)
    assert wait_until(lambda: sup.serving("a"))
    line = sup.line_of("a")
    line.deaf = True
    del sup.wanted["a"]
    sup.reconcile()
    assert sup.watching() == {} and line.thread.is_alive()
    sup.stop()
    line.thread.join(30)
    assert not line.thread.is_alive() and len(line.interrupted_by) == 2 and line.closed_by == ["watch:a"]


def test_a_connection_that_never_will_do_ends_its_worker_and_is_closed(build):
    sup = build("a")
    sup.never_ready = {"a"}
    started(sup)
    assert wait_until(lambda: sup.lines)
    line = sup.line_of("a")
    line.thread.join(30)
    assert not line.thread.is_alive() and sup.watching() == {"a": False}
    assert line.closed_by == ["watch:a"]
    sup.reconcile()                                            # it isn't started again
    assert sup.watching() == {"a": False} and len(sup.lines) == 1
    sup.stop()                                                 # and it never listed its connection to be interrupted
    assert line.interrupted_by == []


def test_a_connection_that_ends_while_still_wanted_is_closed_and_replaced(build):
    sup = build("a")
    sup.serve_once = True
    started(sup)
    assert wait_until(lambda: sup.serving("a"))
    first = sup.line_of("a")
    first.news.set()
    assert wait_until(lambda: first.closed_by == ["watch:a"] and len(sup.lines) == 2)
    second = sup.line_of("a", 1)
    assert wait_until(lambda: second.waiting)
    assert sup.watching() == {"a": True} and sup.said == []
    sup.stop()                                                 # the first is off the list, the second on it
    assert first.interrupted_by == [] and second.interrupted_by == [ME]


def test_a_worker_rests_twice_as_long_each_time_up_to_the_longest(build):
    sup = build("a")
    sup.FIRST_REST, sup.LONGEST_REST = TICK, 60 * TICK         # as 5 seconds is to 300
    sup.refuse_connect = 8
    started(sup)
    assert wait_until(lambda: sup.serving("a"))
    assert [r / TICK for r in sup.rests] == [1, 2, 4, 8, 16, 32, 60, 60]


def test_a_connection_that_serves_starts_the_rests_over(build):
    sup = build("a")
    sup.FIRST_REST, sup.LONGEST_REST = TICK, 60 * TICK
    sup.refuse_connect = 3
    started(sup)
    assert wait_until(lambda: sup.serving("a"))
    assert sup.next_rest("a") == TICK
    sup.line_of("a").drop()
    assert wait_until(lambda: len(sup.lines) == 2 and sup.line_of("a", 1).waiting)
    assert [r / TICK for r in sup.rests] == [1, 2, 4, 1]
    assert sup.line_of("a").closed_by == ["watch:a"]


def test_a_worker_says_what_failed(build):
    sup = build("a")
    sup.refuse_connect = 1
    started(sup)
    assert wait_until(lambda: sup.said == [("failed", "a", "ConnectionRefusedError")])


def test_a_worker_interrupted_as_it_leaves_says_nothing_failed(build):
    sup = build("a")
    started(sup)
    assert wait_until(lambda: sup.serving("a"))
    line = sup.line_of("a")
    sup.stop()
    line.thread.join(30)
    assert not line.thread.is_alive() and line.shut and sup.said == []


@pytest.mark.parametrize("how", ["retired", "stopped"])
def test_a_worker_resting_is_woken_at_once(build, how):
    sup = build("a")
    sup.FIRST_REST = 3600                                      # one left to rest is still resting when the test gives up on it
    sup.refuse_connect = 1
    started(sup)
    assert wait_until(lambda: sup.said)
    [worker] = sup.workers
    if how == "retired":
        del sup.wanted["a"]
        sup.reconcile()
    else:
        sup.stop()
    worker.join(30)
    assert not worker.is_alive()


def test_a_worker_that_finds_itself_unwanted_says_so_and_hands_over(build):
    """Changed with no one to say so: it notices for itself, and asks for a reconcile on its way out."""
    sup = build("a")
    started(sup)
    assert wait_until(lambda: sup.serving("a"))
    old = sup.line_of("a")
    sup.wanted["a"] = "second"                                 # and no reconcile from here
    old.news.set()
    old.thread.join(30)
    assert not old.thread.is_alive() and old.closed_by == ["watch:a"]
    assert sup.said == [("unwanted", "a")]
    assert wait_until(lambda: len(sup.lines) == 2 and sup.line_of("a", 1).waiting)
    new = sup.line_of("a", 1)
    assert new.spec == ("a", "second") and new.thread is not old.thread


def test_it_is_abstract_until_told_what_a_connection_is():
    with pytest.raises(TypeError):
        IdleSupervisor()


def test_it_asks_only_the_standard_library_and_knows_nothing_of_mail():
    source = pathlib.Path(idle_supervisor.__file__).read_text(encoding="utf-8")
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module.split(".")[0])
    assert names <= sys.stdlib_module_names
    assert not {"imap", "mail", "account", "inbox"} & {w.strip(".,:;()`'\"").lower() for w in source.split()}
