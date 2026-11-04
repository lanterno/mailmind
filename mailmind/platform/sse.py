"""The live event stream's hub: what the app tells open tabs, numbered so a tab that reconnects
misses nothing.

`publish(topic, data)` numbers each event and keeps the last RING of them. Each tab's stream is a
Listener that reads the kept events from its own position, so a slow tab falls behind instead of
losing events. A tab that reconnects sends the id of the last event it saw (EventSource sends it
as Last-Event-ID by itself), and its new stream resumes right after it. When the hub no longer
keeps that position, or the id comes from an earlier run of the process, the stream sends one
`resync` event instead, and the tab fetches afresh what it shows.

An id is `<run>-<n>`: n counts the events this process published, from 1, and run is drawn once
per hub, so an id from before a restart never passes for one of this run's.

Data carries ids and counts only, never a subject or an address in a message; the tab fetches
the rest through the authenticated routes. TOPICS is every topic the app publishes, and publish
refuses any other, so the list stays the whole contract the page listens to.

Standard library only: python3 server.py uses it.
"""
import json
import secrets
import threading
from collections import deque
from dataclasses import dataclass

TOPICS = frozenset({
    "sync-done",          # a sync finished for an account
    "new-mail",           # it brought new mail into INBOX
    "reconcile",          # rows of mail gone from the server were pruned, or expiry moved some
    "snooze-wake",        # snoozed mail came back
    "followup",           # the follow-ups changed
    "mute",               # a conversation was muted or unmuted
    "draft-ready",        # a reply draft written ahead is ready
    "outbox",             # a send went out
    "undo-done",          # an undo finished
    "commands-changed",   # a queued move or flag change was applied
})
RESYNC = "resync"         # this tab missed events it can't be told: fetch afresh
RING = 512                # the events kept for tabs that reconnect
MAX_LISTENERS = 8         # each holds a request thread
HEARTBEAT = 20            # seconds between keepalives on a quiet stream
RETRY_MS = 3000           # how soon EventSource reconnects


@dataclass(frozen=True)
class Event:
    id: str
    topic: str
    data: dict

    def frame(self) -> bytes:
        """The event as the stream sends it."""
        return f"id: {self.id}\nevent: {self.topic}\ndata: {json.dumps(self.data)}\n\n".encode()


class Hub:
    def __init__(self, ring: int = RING, max_listeners: int = MAX_LISTENERS, run: str | None = None):
        self.run = run or secrets.token_hex(4)
        self.max_listeners = max_listeners
        self._kept: deque[tuple[int, str, dict]] = deque(maxlen=ring)
        self._n = 0
        self._listeners = 0
        self._cond = threading.Condition()

    def _id(self, n: int) -> str:
        return f"{self.run}-{n}"

    def publish(self, topic: str, data: dict | None = None) -> str:
        """Number the event, keep it, wake every listener. Returns its id."""
        if topic not in TOPICS:
            raise ValueError(f"{topic!r} is not one of the stream's topics")
        with self._cond:
            self._n += 1
            self._kept.append((self._n, topic, dict(data or {})))
            self._cond.notify_all()
            return self._id(self._n)

    def listen(self, last_event_id: str | None = None) -> "Listener | None":
        """A stream for one tab, from just after `last_event_id` (the Last-Event-ID it sent), or
        from now when it sent none. None once max_listeners streams are open."""
        with self._cond:
            if self._listeners >= self.max_listeners:
                return None
            self._listeners += 1
            if not last_event_id:
                return Listener(self, self._n, resync=False)
            n = self._position(last_event_id.strip())
            if n is None:
                return Listener(self, self._n, resync=True)
            return Listener(self, n, resync=False)

    def _position(self, last_event_id: str) -> int | None:
        """The event number `last_event_id` names, if this hub can resume after it."""
        run, _, n = last_event_id.rpartition("-")
        if run != self.run or not n.isdigit() or int(n) > self._n:
            return None
        oldest = self._kept[0][0] if self._kept else self._n + 1
        return int(n) if int(n) >= oldest - 1 else None

    def listening(self) -> int:
        """How many streams are open."""
        with self._cond:
            return self._listeners

    def _leave(self) -> None:
        with self._cond:
            self._listeners -= 1


class Listener:
    """One tab's stream: the events after its position, as they come."""

    def __init__(self, hub: Hub, position: int, resync: bool):
        self._hub = hub
        self._pos = position
        self._resync = resync
        self._closed = False

    @property
    def last_id(self) -> str:
        """The id of the last event this stream has passed on, or would resume after."""
        return self._hub._id(self._pos)

    def opening(self) -> bytes:
        """What the stream sends first: how soon to reconnect, and its position, so a tab that
        drops before any event arrives still resumes from here."""
        return f"retry: {RETRY_MS}\nid: {self.last_id}\n: connected\n\n".encode()

    def next(self, timeout: float) -> list[Event]:
        """The events after this stream's position, waiting up to `timeout` seconds for one; [] if
        none came. A stream that fell behind what the hub keeps gets one resync instead."""
        hub = self._hub
        with hub._cond:
            hub._cond.wait_for(lambda: self._resync or hub._n > self._pos or self._closed, timeout)
            if self._closed:
                return []
            oldest = hub._kept[0][0] if hub._kept else hub._n + 1
            if self._resync or self._pos < oldest - 1:
                self._resync, self._pos = False, hub._n
                return [Event(self.last_id, RESYNC, {})]
            new = [Event(hub._id(n), topic, data) for n, topic, data in hub._kept if n > self._pos]
            if new:
                self._pos = hub._n
            return new

    def close(self) -> None:
        """End this stream; once only."""
        with self._hub._cond:
            if self._closed:
                return
            self._closed = True
            self._hub._cond.notify_all()
        self._hub._leave()


HUB = Hub()


def publish(topic: str, data: dict | None = None) -> str:
    """HUB.publish: the app's one stream hub."""
    return HUB.publish(topic, data)
