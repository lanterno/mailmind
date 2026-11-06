"""The background jobs' timetable.

A job is a name, a round (a function that does one round of the work), the seconds between
rounds, and the delay before the first. Each job runs on a lane, a thread of its own that runs
its rounds one after another, never two at once, so a round that runs long pushes the next one
back instead of piling up behind it. A round may return the seconds until its next round, when it
knows better than the interval (a backlog to work through, a cycle spread across accounts).
One that raises is logged and counted, and the job goes on at its interval. What a round logs carries
an id of that round's.

`kick(name)` runs a job's next round now, `run(name)` runs one in the caller's thread (what a
test does), and `snapshot()` is what /api/debug/jobs shows: each job's timing and how its last
round went. A job's last error is its exception's class name only, since the text can carry a
mail server's reply.

Standard library only: python3 server.py uses it.
"""
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from mailmind.platform.logging import for_round, get_logger

log = get_logger("jobs")


@dataclass
class Job:
    name: str
    round: Callable[[], float | None]
    every: float
    first: float
    runs: int = 0
    failures: int = 0
    running: bool = False
    last_started: float | None = None       # wall-clock seconds
    last_took: float | None = None          # seconds
    last_error: str | None = None           # the exception's class name
    next_at: float | None = None            # wall-clock seconds; None while a round runs
    _turn: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _wake: threading.Event = field(default_factory=threading.Event, repr=False)


def _daemon(target):
    threading.Thread(target=target, name=getattr(target, "__name__", None), daemon=True).start()


class Scheduler:
    def __init__(self, clock=time.time):
        self._jobs: dict[str, Job] = {}
        self._clock = clock
        self._stop = threading.Event()

    def add(self, name: str, round: Callable[[], float | None], *, every: float, first: float = 0.0) -> None:
        """Run `round` every `every` seconds, the first time `first` seconds after start()."""
        if name in self._jobs:
            raise ValueError(f"a job named {name!r} is already scheduled")
        self._jobs[name] = Job(name, round, float(every), float(first))

    def names(self) -> list[str]:
        return list(self._jobs)

    def start(self, spawn: Callable[[Callable[[], None]], None] = _daemon) -> None:
        """Start each job's lane, named job:<name>; `spawn(target)` runs it, by default on a
        daemon thread of that name."""
        for job in self._jobs.values():
            def lane(job=job):
                self._lane(job)
            lane.__name__ = lane.__qualname__ = f"job:{job.name}"
            spawn(lane)

    def run(self, name: str) -> float:
        """One round of `name`, now, in this thread, after any round already running. Returns the
        seconds until its next round."""
        job = self._jobs[name]
        with job._turn:
            job.running, job.next_at = True, None
            job.last_started = self._clock()
            began = time.monotonic()
            nxt = None
            with for_round(name):
                try:
                    nxt = job.round()
                    job.last_error = None
                except Exception as e:
                    job.failures += 1
                    job.last_error = type(e).__name__
                    log.warning("%(job)s failed", {"job": name}, exc_info=e)
                finally:
                    job.runs += 1
                    job.running = False
                    job.last_took = time.monotonic() - began
        return job.every if nxt is None else max(0.0, float(nxt))

    def kick(self, name: str) -> None:
        """Run the job's next round now rather than when it is due."""
        self._jobs[name]._wake.set()

    def stop(self) -> None:
        """End every lane once its round in progress, if any, returns."""
        self._stop.set()
        for job in self._jobs.values():
            job._wake.set()

    def _lane(self, job: Job) -> None:
        delay = job.first
        while not self._stop.is_set():
            job.next_at = self._clock() + delay
            job._wake.wait(delay)
            job._wake.clear()
            if self._stop.is_set():
                return
            delay = self.run(job.name)

    def snapshot(self) -> list[dict]:
        """Each job's interval, runs and failures, when its last round started and how long it took,
        its last error, and when its next round is due."""
        return [{"name": j.name, "every": j.every, "runs": j.runs, "failures": j.failures,
                 "running": j.running, "last_started": j.last_started, "last_took": j.last_took,
                 "last_error": j.last_error, "next_at": j.next_at} for j in self._jobs.values()]
