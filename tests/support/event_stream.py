"""The live event stream, /api/events, read over a raw socket as EventSource would: a GET without the CSRF header, which
EventSource can't send. Its body comes in chunks (uvicorn's HTTP/1.1), read a frame at a time.

Both the API suite's events tests and the ones that run against the app in the test's process read it here.
"""
import socket

# Headers about the connection, not the stream.
CONNECTION_HEADERS = {"connection", "keep-alive", "transfer-encoding", "date", "server"}


class Stream:
    def __init__(self, app, **headers):
        self.sock = socket.create_connection(("127.0.0.1", app.port), timeout=10)
        h = {"Host": f"127.0.0.1:{app.port}", "Accept": "text/event-stream", "Sec-Fetch-Site": "same-origin",
             **headers}
        self.sock.sendall(("GET /api/events HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in h.items())
                           + "\r\n").encode())
        self.f = self.sock.makefile("rb")
        self.status = int(self.f.readline().split()[1])
        self.headers = {}
        while (line := self.f.readline().decode().rstrip("\r\n")):
            k, _, v = line.partition(":")
            self.headers[k.lower()] = v.strip()
        self.body = _Chunked(self.f) if self.headers.get("transfer-encoding") == "chunked" else self.f

    def frame(self):
        """The next frame, without the blank line that ends it. The `hello` that names the build is
        kept apart, since every edit of the page changes it, and has a test of its own."""
        while (frame := self._read()).startswith("event: hello"):
            self.hello = frame
        return frame

    def _read(self):
        lines = []
        while (line := self.body.readline().decode()) not in ("\n", ""):
            lines.append(line.rstrip("\n"))
        return "\n".join(lines)

    def event(self):
        """The next frame that isn't a keepalive: work that outlasts the keepalive interval lets one
        through first."""
        while (frame := self.frame()) == ": keepalive":
            pass
        return frame

    def close(self):
        self.f.close()
        self.sock.close()


class _Chunked:
    """A chunked body, read a line at a time."""

    def __init__(self, f):
        self.f = f
        self.buf = b""

    def readline(self):
        while b"\n" not in self.buf:
            size = self.f.readline().split(b";")[0].strip()
            if not size or int(size, 16) == 0:
                break
            self.buf += self.f.read(int(size, 16))
            self.f.readline()                           # the CRLF after each chunk
        line, sep, self.buf = self.buf.partition(b"\n")
        return line + sep
