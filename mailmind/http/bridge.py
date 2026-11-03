"""Serve an `http.server` handler class over ASGI, without a socket.

The legacy app is one `BaseHTTPRequestHandler` subclass. `HandlerBridge` hands it each request as
the bytes a socket would have carried, on a daemon thread of its own as `ThreadingHTTPServer` does,
so the handler parses the request line and headers itself and sees the real Host, Origin and
token, and a handler still blocked when the process exits doesn't hold the exit. What the handler writes goes back out
as it writes it: the status line and headers as the response start, every later write as a piece
of the body. A response that never ends (the live event stream) or is too big to hold in memory
(the database export) streams as it did over the socket.

When the client goes away, or the server cancels the request as it shuts down, the handler's next
write raises BrokenPipeError, as a closed socket's would.
"""
import io
import logging
import threading
import urllib.parse

import anyio
import anyio.from_thread
import anyio.lowlevel

log = logging.getLogger(__name__)

# Headers about this one connection. The ASGI server frames and closes the connection itself.
_HOP_BY_HOP = {b"connection", b"keep-alive", b"proxy-connection", b"transfer-encoding", b"upgrade",
               b"te", b"trailer"}

_TOO_LARGE = (413, [(b"content-type", b"text/plain; charset=utf-8")], b"payload too large")
_FAILED = (500, [(b"content-type", b"text/plain; charset=utf-8")], b"request failed")


class HandlerBridge:
    """An ASGI app that answers every HTTP request with `handler_class`.

    The request body is read in full before the handler runs, and only up to `max_body` bytes. A
    request that declares a longer body reaches the handler without it, so the handler's own
    length check answers; one that sends a longer body without declaring it is refused here."""

    def __init__(self, handler_class, *, max_body):
        self.handler_class = handler_class
        self.max_body = max_body

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
        async with anyio.create_task_group() as tg:
            tg.start_soon(_watch_for_disconnect, receive, x.gone)
            threading.Thread(target=self._handle, args=(_request_bytes(scope, body), scope, x),
                             name="bridge-request", daemon=True).start()
            try:
                await x.finished.wait()
            finally:
                x.gone.set()            # if the request was cancelled, the handler's next write ends it
                tg.cancel_scope.cancel()

    async def _read_body(self, scope, receive):
        """The request body, b"" when the declared length is over the cap, or None when the body
        runs past the cap without declaring a length."""
        declared = _header(scope, b"content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_body:
                    return b""
            except ValueError:
                return b""                      # the handler answers a malformed length
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                raise _ClientGone
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_body:
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
            log.exception("handler failed on %s %s", scope.get("method"), scope.get("path"))
        try:
            out.finish()
        except BrokenPipeError:
            pass                                # the response was ended, or the event loop is gone
        try:
            x.call_sync(x.finished.set)
        except Exception:
            pass                                # the event loop is gone; nothing waits any more


class _ClientGone(Exception):
    """The client left before its request body arrived."""


class _Exchange:
    """One request's response, shared by the event loop and the handler's thread. Every message
    goes out on the loop, one at a time, and none after the response has ended."""

    def __init__(self, send):
        self._send = send
        self._lock = anyio.Lock()
        self._token = anyio.lowlevel.current_token()
        self.gone = threading.Event()       # the client left, or the request was cancelled
        self.finished = anyio.Event()       # the handler's thread is done with the response
        self.ended = False

    async def emit(self, message):
        async with self._lock:
            if self.ended:
                raise BrokenPipeError("the response has ended")
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                self.ended = True
            await self._send(message)

    async def emit_whole(self, status, headers, body):
        await self.emit({"type": "http.response.start", "status": status,
                         "headers": [*headers, (b"content-length", str(len(body)).encode())]})
        await self.emit({"type": "http.response.body", "body": body, "more_body": False})

    def call(self, func, *args):
        """Run coroutine function `func` on the event loop, from the handler's thread. When the
        loop won't run it (it cancelled the call as it shut down, or has stopped), the handler
        gets BrokenPipeError, as from a socket that closed."""
        try:
            return anyio.from_thread.run(func, *args, token=self._token)
        except BrokenPipeError:
            raise
        except Exception as e:
            self.gone.set()
            log.debug("response write dropped: %s", type(e).__name__)
            raise BrokenPipeError("the server is shutting down") from e

    def call_sync(self, func, *args):
        return anyio.from_thread.run_sync(func, *args, token=self._token)


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


def _request_bytes(scope, body):
    """The request as it arrived on the wire, with its body read in full and no Expect header:
    the ASGI server has already answered any 100-continue."""
    target = scope.get("raw_path") or urllib.parse.quote(scope["path"]).encode()
    if scope.get("query_string"):
        target += b"?" + scope["query_string"]
    lines = [b"%s %s HTTP/%s" % (scope["method"].encode(), target, scope["http_version"].encode())]
    lines += [k + b": " + v for k, v in scope["headers"] if k != b"expect"]
    return b"\r\n".join(lines) + b"\r\n\r\n" + body
