"""Serve an `http.server` handler class over ASGI, without a socket.

The legacy app is one `BaseHTTPRequestHandler` subclass. `HandlerBridge` hands it each request as
the bytes a socket would have carried, in a worker thread, so the handler parses the request line
and headers itself and sees the real Host, Origin and token. What the handler writes goes back out
as it writes it: the status line and headers as the response start, every later write as a piece
of the body. A response that never ends (the live event stream) or is too big to hold in memory
(the database export) streams as it did over the socket.

When the client goes away, the handler's next write raises BrokenPipeError, as a closed socket's
would.
"""
import io
import logging
import threading
import urllib.parse

import anyio.from_thread
import anyio.to_thread

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
        gone = threading.Event()
        async with anyio.create_task_group() as tg:
            tg.start_soon(_watch_for_disconnect, receive, gone)
            await anyio.to_thread.run_sync(self._handle, _request_bytes(scope, body), scope, send, gone)
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

    def _handle(self, raw, scope, send, gone):
        """Run the handler on one request, in a worker thread."""
        out = _ResponseWriter(send, gone)
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
        out.finish()


class _ClientGone(Exception):
    """The client left before its request body arrived."""


class _ResponseWriter:
    """The handler's wfile: parses the status line and headers out of what it writes, then turns
    every write into an ASGI message. Runs in the handler's thread."""

    def __init__(self, send, gone):
        self._send = send
        self._gone = gone
        self._head = b""
        self._started = False

    def write(self, data):
        if self._gone.is_set():
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
        anyio.from_thread.run(self._send, message)

    def finish(self):
        """End the response. A handler that wrote no complete head gets a plain 500."""
        if self._gone.is_set():
            return
        if self._started:
            self._emit({"type": "http.response.body", "body": b"", "more_body": False})
        else:
            if self._head:
                log.error("handler wrote an incomplete response head")
            anyio.from_thread.run(_send_whole, self._send, *_FAILED)


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
