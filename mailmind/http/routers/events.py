"""The live event stream (mailmind.platform.sse): each tab's `Listener` as server-sent events, for as long as the tab stays.

A tab holds none of the pool a route's handler runs on, which requests waiting on IMAP share. Its wait for the next event runs on a
worker thread of AnyIO's, one at a time under a limiter of the stream's own, so how many there are is what the hub lets listen. The
thread is abandoned when the tab hangs up, and closing the listener ends its wait.
"""
from anyio import CapacityLimiter, to_thread
from fastapi import Request
from starlette.responses import StreamingResponse

from mailmind.http import route
from mailmind.http.schemas.common import Failure, NotJson, answers
from mailmind.platform import perimeter, sse, webroot


class _Stream(StreamingResponse):
    """One tab's stream. The listener is taken here, not by the handler, so there is no moment it's held with nothing to give it
    back, and whatever ends the response gives it back: the tab hanging up, the hub ending it, or the server cancelling it."""

    def __init__(self, last_event_id, build):
        super().__init__((), headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
        self._last_event_id, self._build = last_event_id, build

    async def __call__(self, scope, receive, send):
        tab = sse.HUB.listen(self._last_event_id)
        if tab is None:
            return await route.problem(503, "too many listeners")(scope, receive, send)
        self.body_iterator = self._frames(tab)
        try:
            await super().__call__(scope, receive, send)
        finally:
            tab.close()

    async def _frames(self, tab):
        """A tab that reconnects resumes after the Last-Event-ID its EventSource sends, or gets one resync. A keepalive on a quiet
        stream finds a dead socket."""
        yield tab.opening() + sse.hello(self._build)
        its_own = CapacityLimiter(1)
        while True:
            events = await to_thread.run_sync(tab.next, sse.HEARTBEAT, limiter=its_own, abandon_on_cancel=True)
            if tab.closed:
                return
            yield b"".join(e.frame() for e in events) or b": keepalive\n\n"


def router():
    r = route.router()

    @r.get("/api/events", responses=answers(NotJson, {503: Failure}))
    def events(request: Request):
        return _Stream(request.headers.get("Last-Event-ID"), webroot.edition(perimeter.CSRF))

    return r
