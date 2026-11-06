"""Serve an `http.server` handler class over ASGI, without a socket.

The app is one `BaseHTTPRequestHandler` subclass. `HandlerBridge` hands it each request as
the bytes a socket would have carried, on a daemon thread of its own as `ThreadingHTTPServer` does,
so the handler parses the request line and headers itself and sees the real Host, Origin and
token, and a handler still blocked when the process exits doesn't hold the exit. What the handler writes goes back out
as it writes it: the status line and headers as the response start, every later write as a piece
of the body. A response that never ends (the live event stream) or is too big to hold in memory
(the database export) streams as it did over the socket.

When the client goes away, or the server cancels the request as it shuts down, the handler's next
write raises BrokenPipeError, as a closed socket's would. `close()` ends every response already
under way, for a server that is about to stop: the event stream never ends on its own.
"""
import asyncio
import concurrent.futures
import io
import threading
import urllib.parse

import anyio

from mailmind.platform.logging import get_logger

log = get_logger("http.bridge")

# Headers about this one connection. The ASGI server frames and closes the connection itself.
_HOP_BY_HOP = {b"connection", b"keep-alive", b"proxy-connection", b"transfer-encoding", b"upgrade",
               b"te", b"trailer"}

# What a call onto the event loop raises when the loop won't run it. A call the loop cancels as it
# stops comes back as asyncio's CancelledError, which is a BaseException, not an Exception.
_CALL_FAILED = (Exception, asyncio.CancelledError)

# How often a handler's thread, waiting on the event loop, checks that the loop still runs.
_LOOP_CHECK = 0.5

_TOO_LARGE = (413, [(b"content-type", b"text/plain; charset=utf-8")], b"payload too large")
_FAILED = (500, [(b"content-type", b"text/plain; charset=utf-8")], b"request failed")


class HandlerBridge:
    """An ASGI app that answers every HTTP request with `handler_class`.

    The request body is read in full before the handler runs, and only up to
    `body_cap(method, path)` bytes, the most the handler would read for that request (0 if it
    reads none), with `path` as sent, still percent-encoded. A request that declares a longer body
    reaches the handler without it, so the handler's own length check answers at once, without
    waiting for a body it won't read. One that sends a longer body without declaring it is refused
    here."""

    def __init__(self, handler_class, *, body_cap):
        self.handler_class = handler_class
        self.body_cap = body_cap
        self._live = set()

    async def close(self):
        """End every response that has started, now, without waiting for its handler; the
        handler's next write fails. Requests not yet answered are left to finish."""
        for x in list(self._live):
            await x.end()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            raise RuntimeError(f"HandlerBridge serves HTTP only, not {scope['type']!r}")
        try:
            body = await self._read_body(scope, receive)
        except _ClientGone:
            return
        if body is None:
            await _send_whole(send, *_TOO_LARGE)
            return
        x = _Exchange(send)
        self._live.add(x)
        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(_watch_for_disconnect, receive, x.gone)
                threading.Thread(target=self._handle, args=(_request_bytes(scope, body), scope, x),
                                 name="bridge-request", daemon=True).start()
                try:
                    await x.finished.wait()
                finally:
                    x.gone.set()        # if the request was cancelled, the handler's next write ends it
                    tg.cancel_scope.cancel()
        finally:
            self._live.discard(x)

    async def _read_body(self, scope, receive):
        """The request body: b"" when the handler won't read it (over the cap, or a body the
        handler never reads), or None when it runs past the cap without declaring a length."""
        cap = self.body_cap(scope["method"], _raw_path(scope).decode("latin-1"))
        declared = _header(scope, b"content-length")
        if declared is not None:
            try:
                if not 0 <= int(declared) <= cap:
                    return b""
            except ValueError:
                return b""                      # the handler answers a malformed length
        elif cap == 0:
            return b""
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                raise _ClientGone
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > cap:
                return None
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        return b"".join(chunks)

    def _handle(self, raw, scope, x):
        """Run the handler on one request, on the request's own thread."""
        out = _ResponseWriter(x)
        handler = self.handler_class.__new__(self.handler_class)
        handler.rfile = io.BytesIO(raw)
        handler.wfile = out
        handler.client_address = tuple(scope.get("client") or ("127.0.0.1", 0))
        handler.server = None
        handler.request = None
        handler.close_connection = True
        try:
            handler.handle_one_request()
        except BrokenPipeError:
            pass                                # the client left; nothing more to send
        except Exception:
            log.exception("handler failed on %(method)s %(path)s", {"method": scope.get("method"), "path": scope.get("path")})
        try:
            out.finish()
        except BrokenPipeError:
            pass                                # the response was ended, or the event loop is gone
        x.say_finished()


class _ClientGone(Exception):
    """The client left before its request body arrived."""


class _Exchange:
    """One request's response, shared by the event loop and the handler's thread. Every message
    goes out on the loop, one at a time, and none after the response has ended."""

    def __init__(self, send):
        self._send = send
        self._lock = anyio.Lock()
        self._loop = asyncio.get_running_loop()
        self.gone = threading.Event()       # the client left, or the request was cancelled or ended
        self.finished = anyio.Event()       # nothing more will be sent
        self.started = False
        self.ended = False

    async def emit(self, message):
        async with self._lock:
            if self.ended:
                raise BrokenPipeError("the response has ended")
            if message["type"] == "http.response.start":
                self.started = True
            elif not message.get("more_body", False):
                self.ended = True
            await self._send(message)

    async def end(self):
        """End the response here if it has started and not ended, and stop waiting for the
        handler, whose next write fails."""
        async with self._lock:
            if not self.started or self.ended:
                return
            self.gone.set()
            self.ended = True
            await self._send({"type": "http.response.body", "body": b"", "more_body": False})
        self.finished.set()

    async def emit_whole(self, status, headers, body):
        await self.emit({"type": "http.response.start", "status": status,
                         "headers": [*headers, (b"content-length", str(len(body)).encode())]})
        await self.emit({"type": "http.response.body", "body": body, "more_body": False})

    def call(self, func, *args):
        """Run coroutine function `func` on the event loop, from the handler's thread, and wait
        for it. When the loop won't run it to the end (it cancelled the call as it shut down, or
        has stopped or closed), the handler gets BrokenPipeError, as from a socket that closed."""
        try:
            return self._wait(asyncio.run_coroutine_threadsafe(func(*args), self._loop))
        except BrokenPipeError:
            raise
        except _CALL_FAILED as e:
            self.gone.set()
            log.debug("response write dropped", exc_info=e)
            raise BrokenPipeError("the server is shutting down") from e

    def _wait(self, future):
        """The call's result. A loop that has stopped runs nothing more, so rather than wait on it
        for good, give up once it is no longer running."""
        while True:
            try:
                return future.result(timeout=_LOOP_CHECK)
            except concurrent.futures.TimeoutError:
                if future.done():
                    raise                       # the call itself raised it
                if not self._loop.is_running():
                    future.cancel()
                    raise RuntimeError("the event loop has stopped") from None

    def say_finished(self):
        """Tell the event loop the handler is done, from the handler's thread, without waiting:
        a loop that has stopped never runs the call."""
        try:
            self._loop.call_soon_threadsafe(self.finished.set)
        except RuntimeError:
            pass                                # the event loop is closed; nothing waits any more


class _ResponseWriter:
    """The handler's wfile: parses the status line and headers out of what it writes, then turns
    every write into an ASGI message. Runs in the handler's thread."""

    def __init__(self, x):
        self._x = x
        self._head = b""
        self._started = False

    def write(self, data):
        if self._x.gone.is_set():
            raise BrokenPipeError("the client went away")
        data = bytes(data)
        if self._started:
            self._body(data)
            return len(data)
        self._head += data
        end = self._head.find(b"\r\n\r\n")
        if end >= 0:
            head, rest = self._head[:end], self._head[end + 4:]
            self._head = b""
            status, headers = _parse_head(head)
            self._emit({"type": "http.response.start", "status": status, "headers": headers})
            self._started = True
            if rest:
                self._body(rest)
        return len(data)

    def flush(self):
        pass

    def _body(self, data):
        self._emit({"type": "http.response.body", "body": data, "more_body": True})

    def _emit(self, message):
        self._x.call(self._x.emit, message)

    def finish(self):
        """End the response. A handler that wrote no complete head gets a plain 500."""
        if self._x.gone.is_set():
            return
        if self._started:
            self._emit({"type": "http.response.body", "body": b"", "more_body": False})
        else:
            if self._head:
                log.error("handler wrote an incomplete response head")
            self._x.call(self._x.emit_whole, *_FAILED)


def _parse_head(head):
    lines = head.split(b"\r\n")
    status = int(lines[0].split(None, 2)[1])
    headers = []
    for line in lines[1:]:
        name, _, value = line.partition(b":")
        name = name.strip().lower()
        if name and name not in _HOP_BY_HOP:
            headers.append((name, value.strip()))
    return status, headers


async def _send_whole(send, status, headers, body):
    await send({"type": "http.response.start", "status": status,
                "headers": [*headers, (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body, "more_body": False})


async def _watch_for_disconnect(receive, gone):
    while (await receive())["type"] != "http.disconnect":
        pass
    gone.set()


def _header(scope, name):
    for key, value in scope["headers"]:
        if key == name:
            return value
    return None


def _raw_path(scope):
    """The path as the client sent it, still percent-encoded, as http.server hands it on."""
    return scope.get("raw_path") or urllib.parse.quote(scope["path"]).encode()


def _request_bytes(scope, body):
    """The request as it arrived on the wire, with its body read in full and no Expect header:
    the ASGI server has already answered any 100-continue."""
    target = _raw_path(scope)
    if scope.get("query_string"):
        target += b"?" + scope["query_string"]
    lines = [b"%s %s HTTP/%s" % (scope["method"].encode(), target, scope["http_version"].encode())]
    lines += [k + b": " + v for k, v in scope["headers"] if k != b"expect"]
    return b"\r\n".join(lines) + b"\r\n\r\n" + body
