"""A stand-in for an OpenAI-compatible Chat Completions API on a loopback port (#49): what MailMind asks OpenAI, Gemini,
a model on this Mac and any other compatible service through (mailmind.assistant.adapters.openai_chat), at the base URL `url`, which ends
in /v1 as OpenAI's does.

It keeps every request it receives (method, path, headers and JSON body), in order, so a test can check what the app sent.
`seen` keeps them too, whatever a test clears of `requests`, for the canaries' check (support/canaries.py).
It listens on 127.0.0.1, or the loopback address `host` names (::1).

What it answers:
- 401 with OpenAI's words, which quote part of the key back, unless the request carries KEY as an `Authorization: Bearer`
  token; with `key` None (a model on this Mac) it takes any request, with a key or without;
- the next reply a test queued with `queue`, if any, with the response headers it names;
- a 400 naming `response_format` for a request that has one, while `refuse_format` is set, as a service without structured
  output does;
- otherwise, for POST /chat/completions, an answer built from the request's own json_schema (fakes.anthropic_api.instance),
  or STUB_TEXT for a call without one, under the model it asked; and for GET /models, `models`.
"""
import json
import socket
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fakes.anthropic_api import instance

KEY = "sk-test-fake-openai-key-0123456789"     # fictional; the fake accepts this one and no other
STUB_TEXT = "Stub reply from the fake OpenAI-compatible API."
MODELS = ("gpt-6.1-sol", "gpt-6-luna", "text-embedding-4-large", "gpt-6-tts", "whisper-2", "dall-e-4", "gpt-6-realtime",
          "omni-moderation-2", "gpt-6-search-preview", "gpt-6-audio", "gpt-image-2", "gpt-6-transcribe")


@dataclass
class Request:
    method: str
    path: str               # under the base URL: /chat/completions, /models
    headers: dict           # lower-cased names
    body: dict

    @property
    def key(self):
        bearer = self.headers.get("authorization", "")
        return bearer[7:] if bearer.startswith("Bearer ") else ""

    @property
    def system(self):
        return next((m["content"] for m in self.body.get("messages", []) if m["role"] == "system"), None)

    @property
    def prompt(self):
        return next((m["content"] for m in self.body.get("messages", []) if m["role"] == "user"), None)

    @property
    def schema(self):
        return ((self.body.get("response_format") or {}).get("json_schema") or {}).get("schema")


def answer(obj, model="gpt-6.1-sol", finish="stop", **usage):
    """A 200 reply whose text is `obj` (JSON-encoded unless it is already a string), with these usage counts."""
    text = obj if isinstance(obj, str) else json.dumps(obj)
    return 200, {"id": "chatcmpl-1", "object": "chat.completion", "model": model,
                 "choices": [{"index": 0, "finish_reason": finish,
                              "message": {"role": "assistant", "content": text, "refusal": None}}],
                 "usage": {"prompt_tokens": 0, "completion_tokens": 0, **usage}}


def refusal(words="I can't help with that.", model="gpt-6.1-sol"):
    return 200, {"id": "chatcmpl-1", "object": "chat.completion", "model": model,
                 "choices": [{"index": 0, "finish_reason": "stop",
                              "message": {"role": "assistant", "content": None, "refusal": words}}],
                 "usage": {"prompt_tokens": 12, "completion_tokens": 3}}


def error(status, message, headers=None, kind="invalid_request_error"):
    """A refusal with `status`, in OpenAI's form. `headers` go out with it, retry-after for one."""
    out = status, {"error": {"message": message, "type": kind, "param": None, "code": None}}
    return out + (headers,) if headers else out


def gemini_error(status, message, reason=None, kind="INVALID_ARGUMENT", quota=None):
    """A refusal in the form Gemini's OpenAI-compatible endpoint gives it: the error inside a list, with an ErrorInfo detail
    giving `reason` where there is one, and a QuotaFailure naming the quota `quota` where there is one."""
    err = {"code": status, "message": message, "status": kind}
    if reason:
        err["details"] = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason,
                           "domain": "googleapis.com", "metadata": {"service": "generativelanguage.googleapis.com"}}]
    if quota:
        err["details"] = [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
            {"quotaMetric": "generativelanguage.googleapis.com/generate_content_requests", "quotaId": quota}]}]
    return status, [{"error": err}]


def no_quota():
    """OpenAI's refusal for an account with no credit left: a 429 whose code is insufficient_quota."""
    return 429, {"error": {"message": "You exceeded your current quota, please check your plan and billing details.",
                           "type": "insufficient_quota", "param": None, "code": "insufficient_quota"}}


class FakeOpenAI:
    KEY = KEY

    def __init__(self, key=KEY, host="127.0.0.1"):
        self.key = key                 # None: any request goes, as a model on this Mac may take them
        self.requests = []
        self.seen = []                # every request since reset, which a test's clearing of `requests` leaves
        self.models = list(MODELS)
        self.refuse_format = False
        self._queued = []
        self._lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self, method):
                raw = self.rfile.read(int(self.headers.get("content-length") or 0))
                body = json.loads(raw) if raw else {}
                path = self.path.split("?")[0].removeprefix("/v1")
                req = Request(method, path, {k.lower(): v for k, v in self.headers.items()}, body)
                status, out, *extra = fake._reply(req)
                data = json.dumps(out).encode()
                self.send_response(status)
                for name, value in (extra[0] if extra else {}).items():
                    self.send_header(name, value)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                self._answer("POST")

            def do_GET(self):
                self._answer("GET")

            def log_message(self, format, *args):
                pass

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            address_family = socket.AF_INET6 if ":" in host else socket.AF_INET

        self.httpd = Server((host, 0), Handler)
        self.url = f"http://{f'[{host}]' if ':' in host else host}:{self.httpd.server_port}/v1"
        self._thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05},
                                        name="fake-openai", daemon=True)

    def _reply(self, req):
        with self._lock:
            self.requests.append(req)
            self.seen.append(req)
            if (req.method, req.path) not in (("POST", "/chat/completions"), ("GET", "/models")):
                return error(404, "Not found", kind="not_found")
            if self.key is not None and req.key != self.key:
                shown = req.key[:5] + "*" * 8 + req.key[-4:] if req.key else ""
                return error(401, f"Incorrect API key provided: {shown}. You can find your API key at "
                                  "https://platform.example.test/account/api-keys.", kind="invalid_api_key")
            if self._queued:
                reply = self._queued.pop(0)
                return reply(req) if callable(reply) else reply
            if req.path == "/models":
                return 200, {"object": "list", "data": [{"id": m, "object": "model", "owned_by": "example"}
                                                        for m in self.models]}
            if self.refuse_format and "response_format" in req.body:
                return error(400, "response_format of type json_schema is not supported by this model")
        model = req.body.get("model", "")
        return answer(STUB_TEXT if req.schema is None else instance(req.schema), model=model)

    def queue(self, *replies):
        """Answer the next requests with these, in order: (status, body) pairs from answer(), refusal() or error(),
        (status, body, headers) triples, or callables taking the Request and returning one."""
        with self._lock:
            self._queued.extend(replies)

    @property
    def chats(self):
        """The chat completions asked, in order."""
        return [r for r in self.requests if r.path == "/chat/completions"]

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
