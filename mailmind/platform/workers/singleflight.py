"""One run at a time for each key, and none for what is already stored.

The AI reads that cost money (the brief, the delta, the weekly review, a thread summary, a reply
drafted ahead) are each made by the first caller that asks, then stored. A caller that asks while
that run is going waits for it and reads what it stored. The caller that takes the lead reads once
more before it makes: a run that ended between its own cache check and its taking the lead may
have stored the answer already, and making it again would pay twice.

Standard library only: python3 server.py uses it.
"""
import threading
from collections.abc import Callable, Hashable
from typing import TypeVar

T = TypeVar("T")


class SingleFlight:
    def __init__(self):
        self._lock = threading.Lock()
        self._runs: dict[Hashable, threading.Event] = {}

    def run(self, key: Hashable, make: Callable[[], T], read: Callable[[], T | None], *,
            wait: float, recheck: bool = True) -> T | None:
        """What `read()` finds for `key`, else what `make()` returns, with one `make` at a time
        for each key.

        A caller that finds a run going waits up to `wait` seconds for it to end, then returns
        `read()`, which is None when the run stored nothing (it failed, or it is still going).
        The caller that leads returns what `read()` finds, if anything, and otherwise `make()`.
        `recheck=False` skips that read, for a caller that wants the answer made afresh."""
        with self._lock:
            done = self._runs.get(key)
            leads = done is None
            if leads:
                done = self._runs[key] = threading.Event()
        if not leads:
            done.wait(wait)
            return read()
        try:
            if recheck:
                found = read()
                if found is not None:
                    return found
            return make()
        finally:
            with self._lock:
                del self._runs[key]
            done.set()

    def busy(self, key: Hashable) -> bool:
        """Whether a run for `key` is going now."""
        with self._lock:
            return key in self._runs
