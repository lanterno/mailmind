"""mailmind.platform.workers.idle_supervisor: one worker thread per key, kept in line with what is
wanted. Plain threads and a connection that is only an event; tests/unit/mailbox/test_idle_supervisor.py
holds the same through IDLE itself."""
import ast
import pathlib
import sys
import threading
import time

import pytest

from mailmind.platform.workers import idle_supervisor
from mailmind.platform.workers.idle_supervisor import IdleSupervisor
from waiting import wait_until

ME = threading.current_thread().name


class Line:
    """A connection. A worker waits on it until news comes, or until it is interrupted, which makes
    the wait raise, as a socket shut down from another thread does."""

    def __init__(self, key):
        self.key = key
        self.interrupted_by = []
        self.closed_by = []
        self.news = threading.Event()
        self.shut = False
        self.deaf = False                 # it takes no notice of the first interrupt

    def interrupt(self):
        self.interrupted_by.append(threading.current_thread().name)
        if self.deaf:
            self.deaf = False
            return
        self.shut = True
        self.news.set()

    def wait(self):
        self.news.wait(10)
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
        self.threads = []
        self.refuse_connect = 0           # how many connects raise
        self.never_ready = set()          # keys whose connections will never do
        self.unreadable = False
        self.serve_once = False           # a connection serves one wait, then ends
        self.said = []

    def _spawn(self, spec):
        watch = super()._spawn(spec)
        self.threads.append(watch.thread)
        return watch

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
        if self.refuse_connect:
            self.refuse_connect -= 1
            raise ConnectionRefusedError("no one there")
        line = Line(spec[0])
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

    def line_of(self, key, n=0):
        return [line for line in self.lines if line.key == key][n]

    def serving(self, key):
        """Whether `key`'s worker has its connection, and so can be interrupted."""
        watch = self._watchers.get(key)
        return watch is not None and watch.conn is not None


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
    threads = [t for sup in made for t in sup.threads]
    for t in threads:
        t.join(5)
    assert not any(t.is_alive() for t in threads), "a worker didn't stop"


def started(sup):
    sup.begin()
    return sup.reconcile()


def test_each_key_wanted_gets_a_worker_of_its_own(build):
    sup = build("a", "b")
    assert started(sup) == 2
    assert sup.watching() == {"a": True, "b": True}
    assert wait_until(lambda: sup.serving("a") and sup.serving("b"))
    assert sup._watchers["a"].thread.name == "watch:a" and sup._watchers["a"].thread.daemon
    assert sup._watchers["a"].thread is not sup._watchers["b"].thread


def test_a_second_reconcile_with_nothing_changed_leaves_the_workers_be(build):
    sup = build("a")
    started(sup)
    assert wait_until(lambda: sup.serving("a"))
    first = sup._watchers["a"]
    assert sup.reconcile() == 1
    assert sup._watchers["a"] is first and len(sup.lines) == 1 and sup.line_of("a").interrupted_by == []


def test_a_key_no_longer_wanted_is_retired_at_once(build):
    sup = build("a", "b")
    started(sup)
    assert wait_until(lambda: sup.serving("a"))
    worker = sup._watchers["a"].thread
    del sup.wanted["a"]
    assert sup.reconcile() == 1
    worker.join(5)
    assert not worker.is_alive() and set(sup.watching()) == {"b"}
    assert sup.line_of("a").interrupted_by == [ME]


def test_a_key_wanted_differently_gets_a_fresh_worker(build):
    sup = build("a")
    started(sup)
    assert wait_until(lambda: sup.serving("a"))
    old = sup._watchers["a"]
    sup.wanted["a"] = "second"
    sup.reconcile()
    old.thread.join(5)
    new = sup._watchers["a"]
    assert not old.thread.is_alive() and new is not old
    assert new.spec == ("a", "second") and sup.watching() == {"a": True}
    assert wait_until(lambda: len(sup.lines) == 2)


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
    worker = sup._watchers["a"].thread
    sup.stop()
    worker.join(5)
    assert sup.reconcile() == 0 and sup.watching() == {} and len(sup.lines) == 1


def test_when_what_is_wanted_cant_be_told_the_workers_stay_as_they_are(build):
    sup = build("a")
    started(sup)
    first = sup._watchers["a"]
    sup.unreadable = True
    assert sup.reconcile() == 0
    assert sup._watchers["a"] is first and sup.watching() == {"a": True}


def test_stop_interrupts_each_connection_and_leaves_closing_it_to_its_worker(build):
    sup = build("a", "b")
    started(sup)
    assert wait_until(lambda: sup.serving("a") and sup.serving("b"))
    threads = [w.thread for w in sup._watchers.values()]
    sup.stop()
    for t in threads:
        t.join(5)
    assert sup.line_of("a").interrupted_by == [ME] and sup.line_of("b").interrupted_by == [ME]
    assert sup.line_of("a").closed_by == ["watch:a"] and sup.line_of("b").closed_by == ["watch:b"]
    assert sup._conns == [] and sup.watching() == {}


def test_a_retired_worker_still_on_its_way_out_is_interrupted_by_stop_too(build):
    """A retired worker leaves the table at once, but its connection is listed until it has gone."""
    sup = build("a")
    started(sup)
    assert wait_until(lambda: sup.serving("a"))
    worker, line = sup._watchers["a"].thread, sup.line_of("a")
    line.deaf = True
    del sup.wanted["a"]
    sup.reconcile()
    assert sup.watching() == {} and sup._conns == [line] and worker.is_alive()
    sup.stop()
    worker.join(5)
    assert not worker.is_alive() and len(line.interrupted_by) == 2 and line.closed_by == ["watch:a"]


def test_a_connection_that_never_will_do_ends_its_worker_and_is_closed(build):
    sup = build("a")
    sup.never_ready = {"a"}
    started(sup)
    worker = sup._watchers["a"].thread
    worker.join(5)
    assert not worker.is_alive() and sup.watching() == {"a": False}
    assert sup.line_of("a").closed_by == ["watch:a"] and sup._conns == []
    sup.reconcile()                                            # it isn't started again
    assert sup._watchers["a"].thread is worker and len(sup.lines) == 1


def test_a_connection_that_ends_while_still_wanted_is_closed_and_replaced(build):
    sup = build("a")
    sup.serve_once = True
    started(sup)
    assert wait_until(lambda: sup.serving("a"))
    first = sup.line_of("a")
    first.news.set()
    assert wait_until(lambda: first.closed_by == ["watch:a"] and len(sup.lines) == 2)
    assert wait_until(lambda: sup._conns == [sup.line_of("a", 1)])
    assert sup.watching() == {"a": True} and sup.said == []


class Rests(Watching):
    """Writes down each rest and takes none, then quits at the `until`th."""

    def __init__(self, *keys, until):
        super().__init__(*keys)
        self.until = until
        self.rests = []

    def _rest(self, watch, seconds):
        self.rests.append(seconds)
        if len(self.rests) == self.until:
            watch.quit = True


def test_a_worker_rests_twice_as_long_each_time_up_to_the_longest():
    sup = Rests("a", until=8)
    sup.refuse_connect = 100
    sup._worker(("a", "first"))
    assert sup.rests == [5, 10, 20, 40, 80, 160, 300, 300]


def test_a_connection_that_serves_starts_the_rests_over():
    sup = Rests("a", until=4)
    sup.refuse_connect = 3

    def drops(watch, conn, /):
        raise OSError("the line dropped")
    sup._serve = drops
    sup._worker(("a", "first"))
    assert sup.rests == [5, 10, 20, 5]
    assert sup.lines[0].closed_by == [ME]


def test_a_worker_says_what_failed(build):
    sup = build("a")
    sup.refuse_connect = 1
    started(sup)
    assert wait_until(lambda: sup.said == [("failed", "a", "ConnectionRefusedError")])


def test_a_worker_interrupted_as_it_leaves_says_nothing_failed(build):
    sup = build("a")
    started(sup)
    assert wait_until(lambda: sup.serving("a"))
    worker = sup._watchers["a"].thread
    sup.stop()
    worker.join(5)
    assert not worker.is_alive() and sup.line_of("a").shut and sup.said == []


@pytest.mark.parametrize("how", ["retired", "stopped"])
def test_a_worker_resting_is_woken_at_once(build, how):
    sup = build("a")
    sup.FIRST_REST = 60
    sup.refuse_connect = 1
    started(sup)
    assert wait_until(lambda: sup.said)
    worker = sup._watchers["a"].thread
    began = time.monotonic()
    if how == "retired":
        del sup.wanted["a"]
        sup.reconcile()
    else:
        sup.stop()
    worker.join(5)
    assert not worker.is_alive() and time.monotonic() - began < 2


def test_a_worker_that_finds_itself_unwanted_says_so_and_hands_over(build):
    """Changed with no one to say so: it notices for itself, and asks for a reconcile on its way out."""
    sup = build("a")
    started(sup)
    assert wait_until(lambda: sup.serving("a"))
    old, line = sup._watchers["a"], sup.line_of("a")
    sup.wanted["a"] = "second"                                 # and no reconcile from here
    line.news.set()
    old.thread.join(5)
    assert not old.thread.is_alive() and line.closed_by == ["watch:a"]
    assert sup.said == [("unwanted", "a")]
    assert wait_until(lambda: sup.serving("a"))
    assert sup._watchers["a"] is not old and sup._watchers["a"].spec == ("a", "second")


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
