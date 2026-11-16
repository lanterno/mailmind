"""One attempt at an AI provider's HTTP API, on the standard library (#49): where the API is (`Endpoint`), and one request
to it and its whole answer (`attempt`), as the adapter that speaks OpenAI's Chat Completions makes one.

An attempt is a connection of its own to the host the URL names, with no proxy: over TLS verified on the system's roots, or
plain where the URL says http. Its time limit is a deadline for all of it, connecting, asking and reading the whole answer,
however a peer trickles it in, and an answer runs to MOST bytes at most, as the Rust server reads one. Nothing a peer says
reaches the error an attempt that failed raises, which can quote a header back.
"""
import http.client
import json
import socket
import ssl
import threading
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

MOST = 32 << 20                    # bytes: the most an answer may run to, as the Rust server reads one
_PIECE = 64 << 10                  # bytes read at a time, at most


class Answer:
    """What came back: its status, its whole body, and its headers, each name lower-cased."""

    def __init__(self, status: int, body: bytes, headers: Mapping[str, str]):
        self.status, self.body, self.headers = status, body, headers

    def json(self) -> Any:
        try:
            return json.loads(self.body or b"null")
        except ValueError:
            return None


class Unanswered(Exception):
    """Nothing answered, or nothing whole in time: `why` in words that hold nothing a peer said, "timed out", "connection
    refused", or the failure's class."""

    def __init__(self, why: str):
        super().__init__(why)
        self.why = why


@dataclass(frozen=True)
class Endpoint:
    """Where an API is: over TLS or not, its host (an IPv6 address without its brackets), its port, and the path its routes
    hang from, without a trailing /."""
    tls: bool
    host: str
    port: int
    base: str


def endpoint(url: str) -> Endpoint | None:
    """An http or https URL as where its API is, at the scheme's port where it names none, or None for one that names no
    API: another scheme, no host, a user, a blank or a control character, or a port that is empty, no number, or outside 1
    to 65535."""
    url = url.strip()
    if any(ch.isspace() or unicodedata.category(ch) == "Cc" for ch in url):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname or "@" in parts.netloc or parts.netloc.endswith(":"):
        return None
    tls = parts.scheme == "https"
    port = (443 if tls else 80) if port is None else port
    if not 1 <= port <= 65535:
        return None
    return Endpoint(tls, parts.hostname, port, parts.path.rstrip("/"))


class ResponseTooLarge(http.client.HTTPException):
    """An answer longer than MOST."""


def _whole(resp: http.client.HTTPResponse) -> bytes:
    """All of a response's body, read as it comes: ResponseTooLarge once it is longer than MOST, and IncompleteRead where it
    ends short of the length it said."""
    if resp.length is not None and resp.length > MOST:
        raise ResponseTooLarge
    body = bytearray()
    while piece := resp.read1(_PIECE):
        body += piece
        if len(body) > MOST:
            raise ResponseTooLarge
    if resp.length:
        raise http.client.IncompleteRead(b"", resp.length)
    return bytes(body)


def _cut(conn: http.client.HTTPConnection, held: list[socket.socket], expired: threading.Event) -> None:
    """At an attempt's deadline: say so, and end its connection under whatever waits on it (the socket's own, not TLS's,
    so a read in progress fails rather than finds its TLS gone). A response that ends its connection (HTTP/1.0, `Connection:
    close`, or no length) takes the socket from `conn`, so the one `held` since connecting is ended then."""
    expired.set()
    sock = conn.sock or (held[0] if held else None)
    if sock is not None:
        try:
            socket.socket.shutdown(sock, socket.SHUT_RDWR)
        except OSError:
            pass


def attempt(end: Endpoint, method: str, target: str, headers: Mapping[str, str], body: bytes | None,
            timeout: float) -> Answer:
    """One request to `end` for `target` (a path, and any query), its own connection, which `timeout` limits all told:
    connecting, asking and reading the whole answer, of MOST bytes at most. Raises Unanswered when nothing answered, or
    nothing whole in time."""
    conn: http.client.HTTPConnection
    if end.tls:
        conn = http.client.HTTPSConnection(end.host, end.port, timeout=timeout, context=ssl.create_default_context())
    else:
        conn = http.client.HTTPConnection(end.host, end.port, timeout=timeout)
    expired = threading.Event()
    held: list[socket.socket] = []           # the connection's socket, which a response that ends it takes from conn
    resp: http.client.HTTPResponse | None = None
    deadline = threading.Timer(timeout, _cut, (conn, held, expired))
    deadline.daemon = True
    deadline.start()
    try:
        conn.connect()
        held.append(conn.sock)
        if expired.is_set():                 # it ran out while connecting, before there was a socket to end
            raise TimeoutError
        conn.request(method, target, body=body, headers=dict(headers))
        resp = conn.getresponse()
        answer = Answer(resp.status, _whole(resp), {k.lower(): v for k, v in resp.getheaders()})
        if expired.is_set():                 # ended under it, which can look like a body that ended
            raise TimeoutError
        return answer
    except (OSError, http.client.HTTPException, ValueError) as e:     # never its words, which can quote a header
        raise Unanswered("timed out" if expired.is_set() or isinstance(e, TimeoutError) else
                         "connection refused" if isinstance(e, ConnectionRefusedError) else type(e).__name__) from None
    finally:
        deadline.cancel()
        if resp is not None:                 # holds the socket where it ends the connection
            resp.close()
        conn.close()
