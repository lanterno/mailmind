"""One worker thread for each key, kept in line with what is wanted.

A worker holds a connection open and waits on it. `reconcile` starts one for each key wanted, up to
a limit, retires one whose key has gone or is now wanted differently, and leaves the rest be. A
worker whose connection fails rests, longer each time, then opens another. One that finds itself no
longer wanted leaves on its own, and asks for a reconcile on its way out. What a connection is, and
what a worker does with one, is the subclass's to say; this knows nothing of what they watch.

Only a worker closes its connection. Anyone else may only interrupt it, which wakes the worker and
closes nothing: closing a socket another thread may be about to wait on frees its descriptor's number
for the next socket opened, which the worker would then wait on and read from.

Standard library only: python3 server.py uses it.
"""
import abc
import threading
from collections.abc import Callable, Mapping
from typing import Protocol


class Interruptible(Protocol):
    def interrupt(self) -> None:
        """From another thread: end a wait on this connection now, without closing it."""
        ...


class Watch[S, C: Interruptible]:
    """One key's worker: what it was started for, and what stops it."""

    def __init__(self, spec: S, stopping: Callable[[], bool]):
        self.spec = spec
        self._stopping = stopping
        self.quit = False                # retired: its key is gone or now wanted differently
        self.conn: C | None = None       # its connection, while it has one
        self.thread: threading.Thread | None = None

    def leaving(self) -> bool:
        return self.quit or self._stopping()


class IdleSupervisor[S, C: Interruptible](abc.ABC):
    """S is what a worker is started for, C its connection."""
    FIRST_REST = 5                  # seconds a worker rests after a failure, doubling each time
    LONGEST_REST = 300

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._conns: list[C] = []            # the workers' connections, to interrupt on stop()
        self._wake = threading.Condition()   # a worker backing off waits here; stop() and a retirement wake it
        self._watchers: dict[str, Watch[S, C]] = {}    # key -> Watch: the workers begin() and reconcile() run
        self._watch_lock = threading.Lock()
        self._running = False                # begin() has run, and stop() hasn't

    # ── what a subclass says ───────────────────────────────────────────────────────────────────

    @abc.abstractmethod
    def _desired(self) -> Mapping[str, S] | None:
        """Each key wanted now, with what its worker is for. None when that can't be told, and the
        workers stay as they are."""

    @abc.abstractmethod
    def _same(self, running: S, wanted: S, /) -> bool:
        """Whether a worker started for `running` serves `wanted` as it is."""

    @abc.abstractmethod
    def _wanted(self, spec: S, /) -> bool:
        """A worker asks: is what it was started for still wanted, as it was?"""

    @abc.abstractmethod
    def _limit(self) -> int:
        """How many workers may run at once."""

    @abc.abstractmethod
    def _name(self, spec: S, /) -> str:
        """Its thread's name."""

    @abc.abstractmethod
    def _connect(self, spec: S, /) -> C:
        """A new connection. Raising makes the worker rest and try again."""

    @abc.abstractmethod
    def _ready(self, spec: S, conn: C, /) -> bool:
        """Make a new connection ready to serve. False: this one never will be, so the worker ends."""

    @abc.abstractmethod
    def _serve(self, watch: Watch[S, C], conn: C, /) -> None:
        """Wait on the connection and act on what it brings, until watch.leaving() or something has
        changed. The worker then closes it and asks whether it is wanted. Raising makes it rest first."""

    @abc.abstractmethod
    def _close(self, conn: C, /) -> None:
        """Close the connection, as its worker leaves it, however it stands. It raises nothing."""

    def _say_unwanted(self, spec: S, /) -> None:
        """A worker found it isn't wanted, and ends."""

    def _say_failed(self, spec: S, error: Exception, /) -> None:
        """A worker's connection failed while nothing was stopping it."""

    # ── the workers ────────────────────────────────────────────────────────────────────────────

    def _stopping(self) -> bool:
        return self._stop.is_set()

    def _rest(self, watch: Watch[S, C], seconds: float) -> None:
        with self._wake:
            self._wake.wait_for(watch.leaving, timeout=seconds)

    def _worker(self, spec: S, watch: Watch[S, C] | None = None) -> None:
        watch = watch or Watch(spec, self._stopping)
        rest = self.FIRST_REST
        while not watch.leaving():
            if not self._wanted(spec):
                self._say_unwanted(spec)
                self.reconcile()                  # changed with no one to say so: a worker for what's wanted now, if running
                return
            conn = None
            try:
                conn = self._connect(spec)
                if not self._ready(spec, conn):
                    return
                self._conns.append(conn)
                watch.conn = conn                 # set before leaving() is asked: _retire sets quit first
                rest = self.FIRST_REST
                self._serve(watch, conn)
            except Exception as e:
                if not watch.leaving():
                    self._say_failed(spec, e)
                self._rest(watch, rest)
                rest = min(rest * 2, self.LONGEST_REST)
            finally:
                watch.conn = None
                if conn is not None:
                    try:
                        self._conns.remove(conn)
                    except ValueError:
                        pass
                    self._close(conn)

    def _spawn(self, spec: S) -> Watch[S, C]:
        watch: Watch[S, C] = Watch(spec, self._stopping)
        watch.thread = threading.Thread(target=self._worker, args=(spec, watch), name=self._name(spec), daemon=True)
        watch.thread.start()
        return watch

    def _retire(self, watch: Watch[S, C]) -> None:
        """Stop a worker: wake it from a rest, and interrupt its connection (see stop)."""
        with self._wake:
            watch.quit = True
            self._wake.notify_all()
        conn = watch.conn
        if conn is not None:
            conn.interrupt()

    def begin(self) -> None:
        """From now until stop(), reconcile() acts."""
        with self._watch_lock:
            self._running = True

    def reconcile(self, _event: object = None) -> int:
        """Bring the workers in line with what is wanted: retire each whose key has gone or is now
        wanted differently, and start one for each key without one, up to the limit. Before begin()
        and after stop() it does nothing. Returns how many keys are wanted."""
        with self._watch_lock:
            if not self._running or self._stop.is_set():
                return 0
            wanted = self._desired()
            if wanted is None:
                return 0
            for key, watch in list(self._watchers.items()):
                if key not in wanted or not self._same(watch.spec, wanted[key]):
                    self._retire(watch)
                    del self._watchers[key]
            for key, spec in wanted.items():
                if key not in self._watchers and len(self._watchers) < self._limit():
                    self._watchers[key] = self._spawn(spec)
            return len(wanted)

    def watching(self) -> dict[str, bool]:
        """The keys with a worker, each with whether it is still running (one may have ended on a
        connection that will never do)."""
        with self._watch_lock:
            return {key: watch.thread is not None and watch.thread.is_alive()
                    for key, watch in self._watchers.items()}

    def stop(self) -> None:
        self._stop.set()
        with self._watch_lock:
            self._running = False
            self._watchers.clear()
        with self._wake:
            self._wake.notify_all()
        for conn in list(self._conns):
            # Wake a worker blocked reading its connection. Never close it from here: the worker may be
            # about to wait on that descriptor, and once closed its number can go to the next socket
            # opened, which the worker would then wait on (and read from). The worker closes its own
            # connection on the way out.
            conn.interrupt()
