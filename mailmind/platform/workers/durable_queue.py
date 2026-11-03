"""A durable work queue over a table of its own: what the outbox and the offline move queue share.

A row is queued, then claimed by the one worker that flips it to its running state, then
finished. An attempt that fails puts it back in the queue after a backoff that doubles each time
(20 s, 40 s, ... up to 15 minutes), and the last allowed attempt puts it in `error` instead,
where it waits for a person to retry it.

Each row has a done-once column, its gate: set the instant the row's one irreversible step
returns (the outbox's wire send, the queue's IMAP move), before any bookkeeping. At boot a row a
crash left running goes back to the queue, and its gate tells the handler whether that step
already happened, so a crash never repeats it.

The table needs id, state, attempts, next_attempt_at, last_error, the gate column and the
finished-at column. Every method but drain and run_forever runs on the connection it's given and
leaves the commit to its caller. Standard library only: python3 server.py uses it.
"""
import threading

QUEUED, ERROR = "queued", "error"


class DurableQueue:
    def __init__(self, name, table, *, running, finished, finished_at, gate, connect,
                 max_attempts=5, backoff_base=20, backoff_cap=900, poll=30):
        """`name` labels its log lines; `running` and `finished` are the table's own names for
        those states; `connect` opens a connection to the database for drain and run_forever."""
        self.name, self.table = name, table
        self.running, self.finished, self.finished_at, self.gate = running, finished, finished_at, gate
        self.max_attempts, self.backoff_base, self.backoff_cap = max_attempts, backoff_base, backoff_cap
        self.poll = poll
        self._connect = connect
        self._kick = threading.Event()

    def due(self, c, now):
        """The ids of queued rows whose time has come, oldest first."""
        return [r[0] for r in c.execute(
            f"SELECT id FROM {self.table} WHERE state=? AND next_attempt_at<=? ORDER BY id", (QUEUED, now))]

    def claim(self, c, id_):
        """Flip a queued row to running and return it, as it is now. None for every caller but
        the one that wins it, and for a row that's gone or not queued."""
        rows = c.execute(f"UPDATE {self.table} SET state=? WHERE id=? AND state=? RETURNING *",
                         (self.running, id_, QUEUED)).fetchall()
        return rows[0] if rows else None

    def backoff(self, attempts):
        """Seconds to wait after the `attempts`-th attempt failed."""
        return min(self.backoff_cap, self.backoff_base * (2 ** (attempts - 1)))

    def retry_later(self, c, row, error, now):
        """An attempt at `row` failed with `error` (scrubbed: it is stored and shown). Back to the
        queue after the backoff, or to error once it was the last attempt allowed. Returns
        (terminal, delay)."""
        attempts = row["attempts"] + 1
        terminal, delay = attempts >= self.max_attempts, self.backoff(attempts)
        c.execute(f"UPDATE {self.table} SET state=?, attempts=?, next_attempt_at=?, last_error=? WHERE id=?",
                  (ERROR if terminal else QUEUED, attempts, now + delay, error, row["id"]))
        return terminal, delay

    def give_up(self, c, row, error):
        """An attempt at `row` failed in a way no retry can fix: to error at once."""
        c.execute(f"UPDATE {self.table} SET state=?, attempts=?, next_attempt_at=0, last_error=? WHERE id=?",
                  (ERROR, row["attempts"] + 1, error, row["id"]))

    def mark_gate(self, c, id_):
        """The row's irreversible step happened: it never runs again."""
        c.execute(f"UPDATE {self.table} SET {self.gate}=1 WHERE id=?", (id_,))

    def finish(self, c, id_, at):
        c.execute(f"UPDATE {self.table} SET state=?, {self.finished_at}=?, last_error=NULL WHERE id=?",
                  (self.finished, at, id_))

    def reset_stuck(self, c):
        """At boot: rows a crash left running go back to the queue."""
        c.execute(f"UPDATE {self.table} SET state=? WHERE state=?", (QUEUED, self.running))

    def requeue(self, c, id_):
        """A person retries a row in error: back to the queue at once, its error cleared. False
        when it isn't in error."""
        return c.execute(f"UPDATE {self.table} SET state=?, next_attempt_at=0, last_error=NULL "
                         f"WHERE id=? AND state=?", (QUEUED, id_, ERROR)).rowcount == 1

    def cancel_queued(self, c, id_):
        """Delete a row no worker has claimed. False once one has."""
        return c.execute(f"DELETE FROM {self.table} WHERE id=? AND state=?", (id_, QUEUED)).rowcount == 1

    def cancel_unapplied(self, c, id_):
        """Delete a row whose irreversible step hasn't happened, claimed or not: its worker
        re-reads the row before that step and stops when it's gone. False once it happened."""
        return c.execute(f"DELETE FROM {self.table} WHERE id=? AND {self.gate}=0", (id_,)).rowcount == 1

    def kick(self):
        """Wake run_forever now rather than at its next poll."""
        self._kick.set()

    def drain(self, handle, now):
        """Run handle(id) for each row due at `now`. One that raises is logged, and the rest
        still run."""
        c = self._connect()
        try:
            ids = self.due(c, now)
        finally:
            c.close()
        for id_ in ids:
            try:
                handle(id_)
            except Exception as e:
                print(f"[{self.name}] {handle.__name__} #{id_} errored: {type(e).__name__}: {e}", flush=True)

    def run_forever(self, drain, stop=None):
        """The worker: put back what a crash left running, then call drain() whenever kicked,
        and at least every `poll` seconds to catch the retries that come due. drain is the
        worker's own, which calls self.drain with its handler and the time. It ends only once
        `stop`, an Event, is set (and the queue kicked)."""
        c = self._connect()
        try:
            self.reset_stuck(c)
            c.commit()
        finally:
            c.close()
        while True:
            self._kick.wait(timeout=self.poll)
            self._kick.clear()
            if stop is not None and stop.is_set():
                return
            try:
                drain()
            except Exception as e:
                print(f"[{self.name}] drain error: {type(e).__name__}: {e}", flush=True)
