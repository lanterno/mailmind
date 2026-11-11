"""A stand-in for the Claude Messages API on a loopback port, for the app's language model (the
Anthropic SDK, mailmind.assistant.adapters) to call through ANTHROPIC_BASE_URL.

It keeps every request it receives (headers and JSON body), in order, so a test can check what the
app sent: which prompt, which schema, which model and effort, and whether anything was sent at all.

What it answers:
- 401 authentication_error unless the request carries KEY, as an `Authorization: Bearer` token (the
  SDK's way) or as `x-api-key`, as the real API does for a missing or wrong key;
- the next reply a test queued with `queue`, if any, with the response headers it names;
- else what the test's `responder` returns for the request, if it set one and it returns a reply;
- otherwise an answer built from the request's own schema (see `instance`), or STUB_TEXT for a
  call without one. The same request always gets the same answer, so AI routes can be golden-tested
  without scripting each reply.

It holds the Message Batches API too. A batch stays in progress until the test calls `end_batch`, whose
results default to the same answers; `succeeded`, `errored`, `EXPIRED` and `CANCELED` make others. A batch
that has ended can be deleted (`deleted` lists them), and the id of one that is gone answers 404.
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
    method: str = "POST"
    path: str = "/v1/messages"

    @property
    def prompt(self):
        return self.body["messages"][0]["content"]

    @property
    def system(self):
        return self.body.get("system")

    @property
    def schema(self):
        return ((self.body.get("output_config") or {}).get("format") or {}).get("schema")

    @property
    def key(self):
        """The API key it carried, whichever way it was sent."""
        bearer = self.headers.get("authorization", "")
        return self.headers.get("x-api-key") or (bearer[7:] if bearer.startswith("Bearer ") else "")


def answer(obj, stop="end_turn"):
    """A 200 reply whose text is `obj` (JSON-encoded unless it is already a string)."""
    text = obj if isinstance(obj, str) else json.dumps(obj)
    return 200, {"type": "message", "role": "assistant", "model": "claude-opus-5-5", "stop_reason": stop,
                 "content": [{"type": "text", "text": text}],
                 "usage": {"input_tokens": 0, "output_tokens": 0}}


def error(status, kind, message, headers=None):
    """A refusal with `status`. `headers` go out with it, retry-after for one."""
    out = status, {"type": "error", "error": {"type": kind, "message": message}}
    return out + (headers,) if headers else out


def succeeded(obj, **usage):
    """One line of a batch's results: a message whose text is `obj`, with these usage counts."""
    _, message = answer(obj)
    message["usage"].update(usage)
    return {"type": "succeeded", "message": message}


def errored(kind="api_error"):
    return {"type": "errored", "error": {"type": "error", "error": {"type": kind, "message": kind}}}


EXPIRED, CANCELED = {"type": "expired"}, {"type": "canceled"}
BATCHES = "/v1/messages/batches"


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
        self.batches = {}             # id -> {"requests", "status", "results"}
        self.deleted = []             # ids of the batches deleted
        self._lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self, method):
                body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
                req = Request({k.lower(): v for k, v in self.headers.items()}, body, method, self.path)
                status, out, *extra = fake._reply(self.path.split("?")[0], req)
                data = out.encode() if isinstance(out, str) else json.dumps(out).encode()
                self.send_response(status)
                for name, value in (extra[0] if extra else {}).items():
                    self.send_header(name, value)
                self.send_header("content-type", "application/x-jsonl" if isinstance(out, str) else "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                self._answer("POST")

            def do_GET(self):
                self._answer("GET")

            def do_DELETE(self):
                self._answer("DELETE")

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
            if path != "/v1/messages" and not path.startswith(BATCHES):
                return error(404, "not_found_error", "Not found")
            if req.key != KEY:
                return error(401, "authentication_error", "invalid x-api-key")
            if self._queued:
                reply = self._queued.pop(0)
                return reply(req) if callable(reply) else reply
            if path.startswith(BATCHES):
                return self._batch(req.method, path[len(BATCHES):].strip("/"), req)
            responder = self.responder
        reply = responder(req) if responder else None
        if reply is not None:
            return reply
        if req.schema is None:
            return answer(STUB_TEXT)
        return answer(instance(req.schema))

    def _batch(self, method, rest, req):
        batch_id, _, tail = rest.partition("/")
        if method == "POST" and not rest:
            batch_id = f"msgbatch_{len(self.batches) + len(self.deleted) + 1:04}"
            self.batches[batch_id] = {"requests": req.body["requests"], "status": "in_progress", "results": {}}
            return 200, self._view(batch_id)
        if batch_id not in self.batches:
            return error(404, "not_found_error", "no such batch")
        if method == "GET" and not tail:
            return 200, self._view(batch_id)
        if method == "GET" and tail == "results":
            batch = self.batches[batch_id]
            if batch["status"] != "ended":
                return error(400, "invalid_request_error", "the batch has not ended")
            return 200, "".join(json.dumps({"custom_id": i, "result": r}) + "\n" for i, r in batch["results"].items())
        if method == "DELETE":
            if self.batches[batch_id]["status"] != "ended":
                return error(400, "invalid_request_error", "the batch has not ended")
            del self.batches[batch_id]
            self.deleted.append(batch_id)
            return 200, {"id": batch_id, "type": "message_batch_deleted"}
        return error(404, "not_found_error", "Not found")

    def _view(self, batch_id):
        batch = self.batches[batch_id]
        return {"id": batch_id, "type": "message_batch", "processing_status": batch["status"],
                "request_counts": {"processing": 0, "succeeded": 0, "errored": 0, "canceled": 0, "expired": 0},
                "created_at": "2026-11-02T12:00:00Z", "expires_at": "2026-11-03T12:00:00Z",
                "ended_at": None, "archived_at": None, "cancel_initiated_at": None,
                "results_url": f"{self.url}{BATCHES}/{batch_id}/results" if batch["status"] == "ended" else None}

    def end_batch(self, batch_id=None, results=None):
        """End a batch, the latest unless named. `results` maps a custom id to its line (from `succeeded`,
        `errored`, EXPIRED or CANCELED); any other gets an answer built from its schema."""
        with self._lock:
            batch = self.batches[batch_id or list(self.batches)[-1]]
            for r in batch["requests"]:
                params = r["params"]
                schema = ((params.get("output_config") or {}).get("format") or {}).get("schema")
                batch["results"][r["custom_id"]] = (results or {}).get(
                    r["custom_id"], succeeded(STUB_TEXT if schema is None else instance(schema)))
            batch["status"] = "ended"

    def queue(self, *replies):
        """Answer the next requests with these, in order: (status, body) pairs from answer() or
        error(), (status, body, headers) triples, or callables taking the Request and returning
        one."""
        with self._lock:
            self._queued.extend(replies)

    def reset(self):
        with self._lock:
            self.requests.clear()
            self._queued.clear()
            self.responder = None
            self.batches.clear()
            self.deleted.clear()

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
