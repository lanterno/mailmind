"""In-process publish and subscribe: one part of the app tells the others what happened.

An event is a frozen dataclass that carries ids and counts (an account, a row's id), never a
subject or a body. Its class is its topic: `subscribe(Sent, handler)` hears every Sent published,
and nothing else, not even a subclass's. `publish(event)` runs each handler in the publisher's
thread, in the order they subscribed, once the change the event reports has committed. A handler
that raises is logged by its exception's class name and the others still run, since the change
has happened and one listener failing must not undo it or keep the rest from hearing of it.

Nothing here is durable. Work that would be wrong to lose in a crash (a send, a queued move,
assessing new mail) is a row written in the same transaction as the change, which a queue then
works through; an event only tells the rest of the app it happened. Handlers that run inside the
transaction come with the unit of work.

Standard library only: python3 server.py uses it.
"""
import dataclasses
import threading
from collections.abc import Callable
from typing import Any

Handler = Callable[[Any], object]


def _is_event(topic) -> bool:
    return (isinstance(topic, type) and dataclasses.is_dataclass(topic)
            and getattr(topic, "__dataclass_params__").frozen)


class Bus:
    def __init__(self):
        self._handlers: dict[type, tuple[Handler, ...]] = {}
        self._lock = threading.Lock()

    def subscribe(self, topic: type, handler: Handler) -> Callable[[], None]:
        """Call `handler(event)` for every event of class `topic` published from now on. Returns a
        function that unsubscribes it."""
        if not _is_event(topic):
            raise TypeError(f"{topic!r} is not a frozen dataclass, so it can't be an event")
        with self._lock:
            self._handlers[topic] = (*self._handlers.get(topic, ()), handler)

        def unsubscribe():
            with self._lock:
                left = list(self._handlers.get(topic, ()))
                if handler in left:
                    left.remove(handler)
                self._handlers[topic] = tuple(left)
        return unsubscribe

    def publish(self, event) -> None:
        """Tell each of the event's subscribers, in this thread, in the order they subscribed."""
        if not _is_event(type(event)):
            raise TypeError(f"{event!r} is not a frozen dataclass, so it can't be an event")
        with self._lock:
            handlers = self._handlers.get(type(event), ())
        for handler in handlers:
            try:
                handler(event)
            except Exception as e:
                name = getattr(handler, "__qualname__", type(handler).__name__)
                print(f"[bus] {name} failed on {type(event).__name__}: {type(e).__name__}",
                      flush=True)

    def subscribers(self, topic: type) -> tuple[Handler, ...]:
        """The handlers subscribed to `topic`, in order."""
        with self._lock:
            return self._handlers.get(topic, ())


BUS = Bus()


def subscribe(topic: type, handler: Handler) -> Callable[[], None]:
    """BUS.subscribe: the app's one bus."""
    return BUS.subscribe(topic, handler)


def publish(event) -> None:
    """BUS.publish: the app's one bus."""
    BUS.publish(event)
