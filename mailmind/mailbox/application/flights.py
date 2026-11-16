"""Each account's syncs in flight: what is reading its mail into the store now (`mm_mailbox::flights` in the Rust core).
An account removed while one reads stops it at the next message, and what was let go of its mail then goes again as the
last of them lands, so nothing it read after the removal stays.

A flight starts before the sync looks its account up, so a removal either finds it in flight, or comes first and the
sync finds no account to read."""
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field


class AccountGone(Exception):
    """The account a sync reads for was removed: it goes no further."""


def _nothing_yet() -> list[Callable[[], object]]:
    return []


@dataclass
class _Flying:
    count: int = 0
    stopped: bool = False
    then: list[Callable[[], object]] = field(default_factory=_nothing_yet)


class Flight:
    """A flight in the air, which lands as its `with` ends, however the sync ends."""

    def __init__(self, flights: "Flights", account: str):
        self._flights = flights
        self._account = account

    @property
    def stopped(self) -> bool:
        """Whether its account was removed since it started."""
        return self._flights.stopped(self._account)

    def going_on(self) -> None:
        """Raise AccountGone once its account was removed: there is no account to read for."""
        if self.stopped:
            raise AccountGone()


class Flights:
    """The flights of one process, by account (its address in lower case)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held: dict[str, _Flying] = {}

    @contextmanager
    def fly(self, account: str) -> Generator[Flight]:
        """A flight of `account`'s, for the `with` block."""
        key = account.lower()
        with self._lock:
            self._held.setdefault(key, _Flying()).count += 1
        try:
            yield Flight(self, key)
        finally:
            self._landed(key)

    def stop(self, account: str) -> None:
        """The account was removed: each of its flights stops at its next message. None in flight, nothing to stop."""
        with self._lock:
            flying = self._held.get(account.lower())
            if flying is not None:
                flying.stopped = True

    def stopped(self, account: str) -> bool:
        """Whether `account`'s flights were told to stop."""
        with self._lock:
            flying = self._held.get(account.lower())
            return flying is not None and flying.stopped

    def after(self, account: str, then: Callable[[], object]) -> bool:
        """Run `then` once the last of `account`'s flights now in the air has landed: whether one was."""
        with self._lock:
            flying = self._held.get(account.lower())
            if flying is None:
                return False
            flying.then.append(then)
            return True

    def _landed(self, key: str) -> None:
        with self._lock:
            flying = self._held[key]
            flying.count -= 1
            if flying.count > 0:
                return
            del self._held[key]
        for then in flying.then:
            then()
