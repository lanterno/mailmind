"""A durable work queue over a table of its own: what the outbox and the mail commands share.

A row is queued, then claimed by the one worker that flips it to its running state, then
finished. An attempt that fails puts it back in the queue after a backoff that doubles each time
(20 s, 40 s, ... up to 15 minutes), and the last allowed attempt puts it in `error` instead,
where it waits for a person to retry it.

Each row has a done-once column, its gate: set the instant the row's one irreversible step
returns (the outbox's wire send, a command's IMAP move), before any bookkeeping. At boot a row a
crash left running goes back to the queue, and its gate tells the handler whether that step
already happened, so a crash never repeats it.

A queue may name a lane column, the account the row's work goes to. Its drain then runs each
lane's rows in turn on a thread of their own, so a row waiting on one account's server holds up
only that account's rows.

The table needs id, state, attempts, next_attempt_at, last_error, the gate column and the
finished-at column, and the lane column when there is one. Every method but drain and run_forever
runs on the connection it's given and leaves the commit to its caller. Standard library only:
python3 server.py uses it.
"""
import sqlite3
import threading
import time
from collections.abc import Callable

from mailmind.kernel.row import Connection, Row
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger

QUEUED, ERROR = "queued", "error"


class DurableQueue:
    def __init__(self, name: str, table: str, *, running: str, finished: str, finished_at: str, gate: str,
                 connect: Callable[[], sqlite3.Connection], max_attempts: int = 5, backoff_base: int = 20,
                 backoff_cap: int = 900, poll: int = 30, lane: str | None = None, unsure: tuple[str, str] | None = None):
        """`name` labels its log lines and threads; `running` and `finished` are the table's own
        names for those states; `connect` opens a connection to the database for drain and
        run_forever; `lane`, a column, splits drain's rows into lanes; `unsure`, (a column, why),
        names rows a crash may have left with the irreversible step done and no one told."""
        self.name, self.table = name, table
        self.running, self.finished, self.finished_at, self.gate = running, finished, finished_at, gate
        self.max_attempts, self.backoff_base, self.backoff_cap = max_attempts, backoff_base, backoff_cap
        self.poll, self.lane, self.unsure = poll, lane, unsure
        self._uow = UnitOfWork(connect)
        self._log = get_logger(name)
        self._kick = threading.Event()
        self._lanes, self._missed = {}, set()   # lane -> the thread running its rows; lanes a drain passed
        self._lanes_lock = threading.Lock()
        self._worker = threading.local()        # .on in run_forever's thread

    def counts(self) -> dict[str, int]:
        """How many rows stand in each state, on a connection of its own."""
        with self._uow.read() as c:
            return {r[0]: r[1] for r in c.execute(f"SELECT state, COUNT(*) FROM {self.table} GROUP BY state")}

    def due(self, c: Connection, now: float) -> list[int]:
        """The ids of queued rows whose time has come, oldest first."""
        return [r[0] for r in c.execute(
            f"SELECT id FROM {self.table} WHERE state=? AND next_attempt_at<=? ORDER BY id", (QUEUED, now))]

    def claim(self, c: Connection, id_: int) -> Row | None:
        """Flip a queued row to running and return it, as it is now. None for every caller but
        the one that wins it, and for a row that's gone or not queued."""
        rows = c.execute(f"UPDATE {self.table} SET state=? WHERE id=? AND state=? RETURNING *",
                         (self.running, id_, QUEUED)).fetchall()
        return rows[0] if rows else None

    def put_back(self, c: Connection, id_: int) -> None:
        """A claimed row wasn't tried, or its try says nothing against it: back in the queue, due now, with no
        attempt counted."""
        c.execute(f"UPDATE {self.table} SET state=?, next_attempt_at=0 WHERE id=? AND state=?",
                  (QUEUED, id_, self.running))

    def backoff(self, attempts: int) -> int:
        """Seconds to wait after the `attempts`-th attempt failed."""
        return min(self.backoff_cap, self.backoff_base * (2 ** (attempts - 1)))

    def retry_later(self, c: Connection, row: Row, error: str | None, now: int) -> tuple[bool, int]:
        """An attempt at `row` failed with `error` (scrubbed: it is stored and shown). Back to the
        queue after the backoff, or to error once it was the last attempt allowed. Returns
        (terminal, delay)."""
        attempts = row["attempts"] + 1
        terminal, delay = attempts >= self.max_attempts, self.backoff(attempts)
        c.execute(f"UPDATE {self.table} SET state=?, attempts=?, next_attempt_at=?, last_error=? WHERE id=?",
                  (ERROR if terminal else QUEUED, attempts, now + delay, error, row["id"]))
        return terminal, delay

    def give_up(self, c: Connection, row: Row, error: str | None) -> None:
        """An attempt at `row` failed in a way no retry can fix: to error at once."""
        c.execute(f"UPDATE {self.table} SET state=?, attempts=?, next_attempt_at=0, last_error=? WHERE id=?",
                  (ERROR, row["attempts"] + 1, error, row["id"]))

    def mark_gate(self, c: Connection, id_: int) -> None:
        """The row's irreversible step happened: it never runs again."""
        c.execute(f"UPDATE {self.table} SET {self.gate}=1 WHERE id=?", (id_,))

    def finish(self, c: Connection, id_: int, at: str) -> None:
        c.execute(f"UPDATE {self.table} SET state=?, {self.finished_at}=?, last_error=NULL WHERE id=?",
                  (self.finished, at, id_))

    def reset_stuck(self, c: Connection) -> None:
        """At boot: rows a crash left running go back to the queue, but one that may have done its
        irreversible step goes to error, for a person to decide."""
        if self.unsure:
            column, why = self.unsure
            c.execute(f"UPDATE {self.table} SET state=?, next_attempt_at=0, last_error=? "
                      f"WHERE state=? AND {column}=1 AND {self.gate}=0", (ERROR, why, self.running))
        c.execute(f"UPDATE {self.table} SET state=? WHERE state=?", (QUEUED, self.running))

    def requeue(self, c: Connection, id_: int) -> bool:
        """A person retries a row in error: back to the queue at once, its error cleared and its
        attempts counted afresh. False when it isn't in error."""
        return c.execute(f"UPDATE {self.table} SET state=?, attempts=0, next_attempt_at=0, last_error=NULL "
                         f"WHERE id=? AND state=?", (QUEUED, id_, ERROR)).rowcount == 1

    def cancel_queued(self, c: Connection, id_: int) -> bool:
        """Delete a row no worker has claimed. False once one has, and for a row back in the
        queue after its irreversible step (a crash's, or one whose bookkeeping failed)."""
        return c.execute(f"DELETE FROM {self.table} WHERE id=? AND state=? AND {self.gate}=0",
                         (id_, QUEUED)).rowcount == 1

    def cancel_unapplied(self, c: Connection, id_: int) -> bool:
        """Delete a row whose irreversible step hasn't happened, claimed or not: its worker
        re-reads the row before that step and stops when it's gone. False once it happened."""
        return c.execute(f"DELETE FROM {self.table} WHERE id=? AND {self.gate}=0", (id_,)).rowcount == 1

    def kick(self) -> None:
        """Wake run_forever now rather than at its next poll."""
        self._kick.set()

    def drain(self, handle: Callable[[int], object], now: float) -> None:
        """Run handle(id) for each row due at `now`, oldest first. One that raises is logged,
        and the rest still run.

        With a lane column, each lane's rows run in turn on a thread of their own. In
        run_forever's thread, drain starts them and returns, and leaves a lane still busy with an
        earlier drain's rows be: that lane kicks the queue as it ends, and the next drain runs
        them. Anywhere else (a test, a script) drain waits for a busy lane before it runs that
        lane's rows, and for every lane it ran before it returns."""
        with self._uow.read() as c:
            if not self.lane:
                lanes = {None: self.due(c, now)}
            else:
                lanes = {}
                for id_, lane in c.execute(f"SELECT id, {self.lane} FROM {self.table} WHERE state=? "
                                           f"AND next_attempt_at<=? ORDER BY id", (QUEUED, now)):
                    lanes.setdefault(lane, []).append(id_)
        if not self.lane:
            self._run(handle, lanes[None])
            return
        wait = not getattr(self._worker, "on", False)
        started = []
        for lane, ids in lanes.items():
            while True:
                with self._lanes_lock:
                    busy = self._lanes.get(lane)
                    if busy is None:
                        t = threading.Thread(target=self._run_lane, args=(lane, ids, handle),
                                             name=f"{self.name}:{lane}", daemon=True)
                        self._lanes[lane] = t
                        t.start()
                        started.append(t)
                        break
                    if not wait:
                        self._missed.add(lane)
                        break
                busy.join()
        if wait:
            for t in started:
                t.join()

    def _run(self, handle, ids):
        for id_ in ids:
            try:
                handle(id_)
            except Exception as e:
                self._log.error("%(handler)s #%(id)s errored", {"handler": handle.__name__, "id": id_}, exc_info=e)

    def _run_lane(self, lane, ids, handle):
        try:
            self._run(handle, ids)
        finally:
            with self._lanes_lock:
                if self._lanes.get(lane) is threading.current_thread():
                    del self._lanes[lane]
                missed = lane in self._missed
                self._missed.discard(lane)
            if missed:
                self.kick()

    def until_due(self, c: Connection, now: float, looked: float | None = None) -> float:
        """Seconds from `now` until the next queued row comes due, at most `poll`, and none if one
        has come due since the last drain began to look (`looked`, by default `now`). That drain may
        have missed it, and no kick is coming. A row due before it looked isn't waited for: a kick
        brings the drain that takes it (a busy lane's, as it ends), and the poll a drain that
        wouldn't (read-only's), so the worker never goes round for it."""
        looked = now if looked is None else looked
        r = c.execute(f"SELECT MIN(next_attempt_at) FROM {self.table} WHERE state=? AND next_attempt_at>?",
                      (QUEUED, looked)).fetchone()
        return self.poll if r is None or r[0] is None else max(0, min(self.poll, r[0] - now))

    def _wait(self, looked):
        try:
            with self._uow.read() as c:
                return self.until_due(c, time.time(), looked)
        except Exception as e:                   # the poll still comes
            self._log.warning("next due", exc_info=e)
            return self.poll

    def run_forever(self, drain: Callable[[], object], stop: threading.Event | None = None) -> None:
        """The worker: put back what a crash left running, then call drain() whenever kicked,
        and when the next queued row comes due (a send's Undo window ending, a retry's backoff),
        at least every `poll` seconds. drain is the worker's own, which calls self.drain with its
        handler and the time. It ends only once `stop`, an Event, is set (and the queue kicked)."""
        self._worker.on = True
        with self._uow.write() as c:
            self.reset_stuck(c)
        looked = time.time()
        while True:
            self._kick.wait(timeout=self._wait(looked))
            self._kick.clear()
            if stop is not None and stop.is_set():
                return
            looked = time.time()
            try:
                drain()
            except Exception as e:
                self._log.error("drain error", exc_info=e)
