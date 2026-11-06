"""How much MailMind may spend on AI, and when. The Gateway asks the governor before every call.

- Kinds (domain.features). An interactive call is one someone asked for. Background work is
  triage. A speculative call is a reply drafted ahead, made only in case it is wanted.
- Slots. Two interactive calls at a time and, apart from those, two background or speculative
  ones, so a backlog never makes a click wait.
- Yielding. Background and speculative work doesn't start while an interactive call runs, nor for
  YIELD_SECONDS after one ends: the user is in the middle of something.
- Presence. Speculative work runs only while someone has used the app in the last
  PRESENCE_SECONDS (the server says so with saw_someone()), so a tab left open over a holiday
  doesn't keep drafting.
- The daily limit, counted across every call by the usage ledger. At the limit, background and
  speculative work stops for the day. An interactive call raises OverLimit instead of being sent,
  unless the user has given leave to go over (allowing_over). The page asks first.
- The breaker. A permanent rejection, a 4xx that sending again won't change, says the request
  itself won't do. A rejected key (401 or 403) stops every background call at once, and
  TRIP_AFTER in a row for one feature and model stop that feature's. It stays open until the
  model in use changes, which a new key, base URL or transport does. Interactive calls still go,
  so the user sees the error.

Nothing here is durable: a restart closes the breaker and forgets the last interactive call. The
day's spending is the ledger's, so the limit holds across a restart.
"""
import contextvars
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any

from mailmind.assistant.application.ports import Deferred, LanguageModel, ModelError, OverLimit
from mailmind.assistant.domain.features import INTERACTIVE, SPECULATIVE

YIELD_SECONDS = 45
PRESENCE_SECONDS = 45 * 60
TRIP_AFTER = 3                      # permanent rejections in a row, for one feature and model
_AUTH = frozenset({401, 403})       # the key itself was refused: no feature will do better
_EVERY = "*"                        # the breaker's scope for a refused key

_over: contextvars.ContextVar[bool] = contextvars.ContextVar("mailmind_ai_may_go_over", default=False)


@contextmanager
def allowing_over(allow: bool = True) -> Generator[None]:
    """Within this, an interactive call may go over the daily limit: the user said so."""
    token = _over.set(allow)
    try:
        yield
    finally:
        _over.reset(token)


class CostGovernor:
    def __init__(self, spent: Callable[[], int], limit: Callable[[], int],
                 clock: Callable[[], float] = time.time, interactive_slots: int = 2,
                 background_slots: int = 2, reserved: Callable[[], int] = lambda: 0):
        """`spent()` is today's spending and `limit()` the daily limit, both in micro-dollars.
        `reserved()` is what batches still out may cost: background work counts it as spent, and
        someone asking for something doesn't, since a backlog shouldn't stand in their way."""
        self._spent, self._limit, self._clock, self._reserved = spent, limit, clock, reserved
        self._interactive = threading.BoundedSemaphore(interactive_slots)
        self._background = threading.BoundedSemaphore(background_slots)
        self._lock = threading.Lock()
        self._running = 0                       # interactive calls in flight
        self._last_interactive = float("-inf")  # when the last one ended
        self._last_seen = clock()               # starting up counts as someone being here
        self._rejected: dict[tuple[str, str], int] = {}    # (feature, model) -> in a row
        self._open: dict[str | tuple[str, str], LanguageModel] = {}   # scope -> the model it opened under

    def limit_micros(self) -> int:
        """The daily limit, in micro-dollars."""
        return self._limit()

    def saw_someone(self) -> None:
        """Someone is using the app now."""
        self._last_seen = self._clock()

    def held_back(self, kind: str, feature: str, model: str, instance: LanguageModel) -> str | None:
        """Why a call of `kind` for `feature` may not start now, or None when it may. An
        interactive call is never held back here: only OverLimit stops one."""
        if kind == INTERACTIVE:
            return None
        now = self._clock()
        with self._lock:
            if self._running or now - self._last_interactive < YIELD_SECONDS:
                return "yielding"
            if kind == SPECULATIVE and now - self._last_seen > PRESENCE_SECONDS:
                return "away"
            if self._is_open(feature, model, instance):
                return "breaker"
        return "limit" if self.room() <= 0 else None    # a ledger that can't be read stops work, not starts it

    def room(self) -> int:
        """What today's limit leaves for background work, in micro-dollars."""
        spent, limit = self._today()
        booked = self._booked()
        return 0 if spent is None or booked is None else max(0, limit - spent - booked)

    @contextmanager
    def call(self, kind: str, feature: str, model: str, instance: LanguageModel) -> Generator[None]:
        """Hold a slot for one call, or raise why it can't be made now: OverLimit for an
        interactive call at the limit without leave to go over, Deferred for background work."""
        if kind == INTERACTIVE:
            spent, limit = self._today()
            if spent is not None and spent >= limit and not _over.get():
                raise OverLimit(spent, limit)
            with self._interactive:
                with self._lock:
                    self._running += 1
                try:
                    yield
                finally:
                    with self._lock:
                        self._running -= 1
                        self._last_interactive = self._clock()
            return
        reason = self.held_back(kind, feature, model, instance)
        if reason:
            raise Deferred(reason)
        with self._background:
            reason = self.held_back(kind, feature, model, instance)   # it may have waited for the slot
            if reason:
                raise Deferred(reason)
            yield

    def heard(self, feature: str, model: str, instance: LanguageModel, error: ModelError | None) -> None:
        """How a call went: None for a reply. A reply resets the feature's count of rejections in a
        row; a transient failure leaves it as it was."""
        key = (feature, model)
        with self._lock:
            if error is None:
                self._rejected.pop(key, None)
                return
            if not error.permanent:
                return
            if error.status in _AUTH:
                opened = self._open.get(_EVERY) is not instance
                self._open[_EVERY] = instance
                scope = "every background call"
            else:
                n = self._rejected.get(key, 0) + 1
                if n < TRIP_AFTER:
                    self._rejected[key] = n
                    return
                self._rejected.pop(key, None)
                opened = self._open.get(key) is not instance
                self._open[key] = instance
                scope = f"{feature} on {model}"
        if opened:
            print(f"[ai] the API keeps rejecting requests: {scope} stops until the key or the model "
                  f"changes ({error.status})", flush=True)

    def forget(self) -> None:
        """Back to how a fresh start finds things: no interactive call lately, someone here now, the
        breaker closed. The tests' state reset calls it; calls in flight keep their slots."""
        with self._lock:
            self._last_interactive = float("-inf")
            self._last_seen = self._clock()
            self._rejected.clear()
            self._open.clear()

    def snapshot(self) -> dict[str, Any]:
        """What /api/debug/ai shows: today's spending and limit, the calls in flight, how long since
        the last interactive one and since someone was here, and where the breaker is open."""
        spent, limit = self._today()
        now = self._clock()
        with self._lock:
            return {"spent_today_micros": spent, "limit_micros": limit, "reserved_micros": self._booked(),
                    "interactive_running": self._running,
                    "since_interactive": None if self._last_interactive == float("-inf")
                    else round(now - self._last_interactive, 1),
                    "since_seen": round(now - self._last_seen, 1),
                    "breaker": sorted(_scope_name(s) for s in self._open)}

    def _is_open(self, feature: str, model: str, instance: LanguageModel) -> bool:
        for scope in (_EVERY, (feature, model)):
            under = self._open.get(scope)
            if under is None:
                continue
            if under is instance:
                return True
            del self._open[scope]               # the model changed since it opened: try again
        return False

    def _booked(self) -> int | None:
        try:
            return self._reserved()
        except Exception as e:
            print(f"[ai] what batches may cost can't be read: {type(e).__name__}", flush=True)
            return None

    def _today(self) -> tuple[int | None, int]:
        """(today's spending, or None when the ledger can't be read; the limit)."""
        try:
            spent = self._spent()
        except Exception as e:
            print(f"[ai] the usage ledger can't be read: {type(e).__name__}", flush=True)
            spent = None
        return spent, self._limit()


def _scope_name(scope: str | tuple[str, str]) -> str:
    return "every feature" if isinstance(scope, str) else f"{scope[0]} on {scope[1]}"
