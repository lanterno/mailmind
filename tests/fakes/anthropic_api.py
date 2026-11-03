"""A stand-in for the Claude Messages API on a loopback port, for claude_api.py to call through
ANTHROPIC_BASE_URL.

It keeps every request it receives (headers and JSON body), in order, so a test can check what the
app sent: which prompt, which schema, which model and effort, and whether anything was sent at all.

What it answers:
- 401 authentication_error unless the request carries KEY, as the real API does for a missing or
  wrong key;
- the next reply a test queued with `queue`, if any;
- else what the test's `responder` returns for the request, if it set one and it returns a reply;
- otherwise an answer built from the request's own schema (see `instance`), or STUB_TEXT for a
  call without one. The same request always gets the same answer, so AI routes can be golden-tested
  without scripting each reply.
"""
import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEY = "sk-ant-test-fake-key"          # fictional; the fake accepts this one and no other
STUB_TEXT = "Stub reply from the fake Claude API."


@dataclass
class Request:
    headers: dict           # lower-cased names
    body: dict

    @property
    def prompt(self):
        return self.body["messages"][0]["content"]

    @property
    def schema(self):
        return ((self.body.get("output_config") or {}).get("format") or {}).get("schema")


def answer(obj, stop="end_turn"):
    """A 200 reply whose text is `obj` (JSON-encoded unless it is already a string)."""
    text = obj if isinstance(obj, str) else json.dumps(obj)
    return 200, {"type": "message", "role": "assistant", "model": "claude-opus-5-5", "stop_reason": stop,
                 "content": [{"type": "text", "text": text}],
                 "usage": {"input_tokens": 0, "output_tokens": 0}}


def error(status, kind, message):
    return status, {"type": "error", "error": {"type": kind, "message": message}}


def instance(schema, name="value"):
    """A deterministic value that satisfies `schema`: the first enum value, "stub <name>" for a
    string, 1 for a number, false for a boolean, one item for an array, and every property of
    an object."""
    if not isinstance(schema, dict):
        return None
    for alt in ("anyOf", "oneOf"):
        if schema.get(alt):
            return instance(schema[alt][0], name)
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "null")
    if kind == "object":
        return {k: instance(s, k) for k, s in (schema.get("properties") or {}).items()}
    if kind == "array":
        return [instance(schema.get("items") or {}, name)]
    if kind == "string":
        return f"stub {name}"
    if kind in ("integer", "number"):
        return 1
    if kind == "boolean":
        return False
    return None


class FakeAnthropic:
    KEY = KEY

    def __init__(self):
        self.requests = []
        self._queued = []
        self.responder = None         # optional: Request -> reply, or None for the default
        self._lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
                req = Request({k.lower(): v for k, v in self.headers.items()}, body)
                status, out = fake._reply(self.path, req)
                data = json.dumps(out).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format, *args):
                pass

        class Server(ThreadingHTTPServer):
            daemon_threads = True

            def process_request(self, request, client_address):
                threading.Thread(target=self.process_request_thread, args=(request, client_address),
                                 name="fake-anthropic-request", daemon=True).start()

        self.httpd = Server(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        self._thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05},
                                        name="fake-anthropic", daemon=True)

    def _reply(self, path, req):
        with self._lock:
            self.requests.append(req)
            if path != "/v1/messages":
                return error(404, "not_found_error", "Not found")
            if req.headers.get("x-api-key") != KEY:
                return error(401, "authentication_error", "invalid x-api-key")
            if self._queued:
                reply = self._queued.pop(0)
                return reply(req) if callable(reply) else reply
            responder = self.responder
        reply = responder(req) if responder else None
        if reply is not None:
            return reply
        if req.schema is None:
            return answer(STUB_TEXT)
        return answer(instance(req.schema))

    def queue(self, *replies):
        """Answer the next requests with these, in order: (status, body) pairs from answer() or
        error(), or callables taking the Request and returning one."""
        with self._lock:
            self._queued.extend(replies)

    def reset(self):
        with self._lock:
            self.requests.clear()
            self._queued.clear()
            self.responder = None

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
