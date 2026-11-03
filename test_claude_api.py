"""The Claude API engine (claude_api.py, and summarize's choice between it and the CLI): what a
request carries, how replies, refusals and errors come back, and when calls are retried.
Hermetic — a local stub server stands in for api.anthropic.com and the key is fake.
All addresses are fictional.
Run: python3 test_claude_api.py"""
import os
import json
import tempfile
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

for var in ("MAILMIND_AI", "MAILMIND_MODEL", "MAILMIND_NO_SCHEMA"):
    os.environ.pop(var, None)
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-key"     # never the real one, even by accident

import claude_api
import summarize
from mailmind.platform import settings

settings.configure(settings.Settings(home=tempfile.mkdtemp()))   # the real config may hold a key

_seen = []        # (headers, body) per request, oldest first
_replies = []     # (status, headers, body) queued per test; the default is a plain JSON answer


class _Stub(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        _seen.append(({k.lower(): v for k, v in self.headers.items()}, body))
        status, headers, out = _replies.pop(0) if _replies else (200, {}, _answer({"summary": "ok"}))
        data = json.dumps(out).encode()
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        pass


_server = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
threading.Thread(target=_server.serve_forever, daemon=True).start()
STUB = f"http://127.0.0.1:{_server.server_port}"


def _answer(obj, stop="end_turn"):
    return {"type": "message", "role": "assistant", "model": "claude-opus-5-5", "stop_reason": stop,
            "content": [{"type": "text", "text": json.dumps(obj) if not isinstance(obj, str) else obj}]}


def _error(kind, message):
    return {"type": "error", "error": {"type": kind, "message": message}}


def _fresh(*replies):
    os.environ["ANTHROPIC_BASE_URL"] = STUB
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-key"
    for var in ("MAILMIND_AI", "MAILMIND_NO_SCHEMA"):
        os.environ.pop(var, None)
    if os.path.exists(settings.current().config):
        os.remove(settings.current().config)
    summarize.MODEL = None
    _seen.clear()
    _replies[:] = list(replies)


def _config(**top):
    with open(settings.current().config, "w") as f:
        json.dump({"accounts": [], **top}, f)


ROW = {"pk": 7, "from_name": "Mira Okafor", "from_addr": "mira@example.test",
       "subject": "Saturday market", "date_iso": "2026-10-30T09:00:00+00:00",
       "snippet": "Could you bring the folding table on Saturday? Let me know by Friday."}


# ── what a request carries ────────────────────────────────────────────────────
def test_request_carries_key_version_and_fallback():
    _fresh()
    summarize.call_claude_json("Triage this.", summarize.TRIAGE_SCHEMA)
    (headers, body), = _seen
    assert headers["x-api-key"] == "sk-ant-test-key", headers
    assert headers["anthropic-version"] == "2026-10-21", headers
    assert headers["anthropic-beta"] == "server-side-fallback-2026-07-01", headers
    assert body["model"] == "claude-opus-5-5" and body["fallbacks"] == "default", body
    assert body["messages"] == [{"role": "user", "content": "Triage this."}], body
    assert not {"temperature", "top_p", "top_k", "thinking"} & set(body), body

def test_schema_goes_out_closed_and_without_bounds():
    _fresh()
    summarize.call_claude_json("Triage this.", summarize.TRIAGE_SCHEMA)
    fmt = _seen[0][1]["output_config"]["format"]
    assert fmt["type"] == "json_schema", fmt
    sent = fmt["schema"]
    assert sent["additionalProperties"] is False and sent["required"] == ["summary", "importance", "category"]
    assert sent["properties"]["importance"] == {"type": "integer"}, sent
    assert sent["properties"]["category"]["enum"][0] == "action-needed"
    assert summarize.TRIAGE_SCHEMA["properties"]["importance"]["maximum"] == 5   # the original is untouched

def test_nested_objects_are_closed_and_property_names_kept():
    nested = {"type": "object", "properties": {
        "maximum": {"type": "string", "maxLength": 10},                 # a property that shares a keyword's name
        "groups": {"type": "array", "items": {"type": "object", "properties": {"n": {"type": "integer", "minimum": 0}}}}}}
    out = claude_api.api_schema(nested)
    assert out["properties"]["maximum"] == {"type": "string"}, out
    item = out["properties"]["groups"]["items"]
    assert item["additionalProperties"] is False and item["properties"]["n"] == {"type": "integer"}, item

def test_triage_runs_at_low_effort_and_the_rest_at_medium():
    _fresh(*[(200, {}, _answer({"summary": "Mira asks for the table.", "importance": 4, "category": "personal"}))])
    summarize.summarize_one(ROW)
    summarize.call_claude_json("Draft a reply.", {"type": "object", "properties": {"body": {"type": "string"}}})
    assert [b["output_config"]["effort"] for _, b in _seen] == ["low", "medium"], _seen

def test_model_override_and_models_without_effort_or_fallback():
    _fresh()
    summarize.MODEL = "claude-haiku-4-5"
    summarize.call_claude_json("Triage this.", summarize.TRIAGE_SCHEMA)
    summarize.MODEL = "claude-haiku-5-5"
    summarize.call_claude_json("Triage this.", summarize.TRIAGE_SCHEMA)
    (h_old, old), (h_new, new) = _seen
    assert old["model"] == "claude-haiku-4-5" and "effort" not in old["output_config"], old
    assert new["model"] == "claude-haiku-5-5" and new["output_config"]["effort"] == "medium", new
    assert "fallbacks" not in old and "fallbacks" not in new
    assert "anthropic-beta" not in h_old and "anthropic-beta" not in h_new
    assert summarize.model_label() == "claude-haiku-5-5"

def test_no_schema_mode_sends_no_format_and_parses_loose_json():
    _fresh((200, {}, _answer('Here you go: {"summary": "loose", "importance": 2, "category": "info"}')))
    os.environ["MAILMIND_NO_SCHEMA"] = "1"
    obj = summarize.call_claude_json("Triage this.", summarize.TRIAGE_SCHEMA)
    assert obj["summary"] == "loose", obj
    assert "format" not in _seen[0][1]["output_config"], _seen[0][1]


# ── how replies come back ─────────────────────────────────────────────────────
def test_triage_stores_the_structured_reply():
    _fresh((200, {}, _answer({"summary": "Mira asks for the table.", "importance": 9,
                              "category": "action-needed", "action": "Reply by Friday"})))
    pk, payload = summarize.summarize_one(ROW)
    assert pk == 7 and payload == ("Mira asks for the table.", 5, "action-needed",
                                   "Reply by Friday", "claude-opus-5-5"), payload

def test_plain_text_call_returns_the_text():
    _fresh((200, {}, _answer("  The thread is about a market stall.  ")))
    assert summarize.call_claude("Summarize this thread.") == "The thread is about a market stall."
    assert "output_config" in _seen[0][1] and "format" not in _seen[0][1]["output_config"]

def test_refusal_reads_as_an_empty_reply():
    _fresh((200, {}, {"stop_reason": "refusal", "content": [],
                      "stop_details": {"type": "refusal", "category": "cyber"}}),
           (200, {}, {"stop_reason": "refusal", "content": []}))
    assert summarize.call_claude_json("Triage this.", summarize.TRIAGE_SCHEMA) == {}
    _, payload = summarize.summarize_one(ROW)
    assert payload is not None and payload[-1] == "parse-error", payload   # stored once, not retried forever

def test_only_the_answer_after_a_fallback_block_counts():
    reply = {"stop_reason": "end_turn", "content": [
        {"type": "text", "text": '{"summary": "half'},
        {"type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "claude-opus-4-8"}},
        {"type": "text", "text": '{"summary": "whole", "importance": 3, "category": "info"}'}]}
    _fresh((200, {}, reply))
    assert summarize.call_claude_json("Triage this.", summarize.TRIAGE_SCHEMA)["summary"] == "whole"


# ── errors and retries ────────────────────────────────────────────────────────
def test_rate_limit_is_retried():
    _fresh((429, {"retry-after": "0"}, _error("rate_limit_error", "slow down")))
    assert summarize.call_claude_json("Triage this.", summarize.TRIAGE_SCHEMA) == {"summary": "ok"}
    assert len(_seen) == 2

def test_bad_key_raises_without_retry():
    _fresh((401, {}, _error("authentication_error", "invalid x-api-key")))
    try:
        summarize.call_claude_json("Triage this.", summarize.TRIAGE_SCHEMA)
    except RuntimeError as e:
        assert "401" in str(e) and "invalid x-api-key" in str(e) and "sk-ant" not in str(e), e
    else:
        raise AssertionError("a 401 must raise")
    assert len(_seen) == 1

def test_failed_triage_is_left_for_the_next_run():
    _fresh((400, {}, _error("invalid_request_error", "bad")))
    assert summarize.summarize_one(ROW) == (7, None)

def test_persistent_overload_gives_up_after_two_retries():
    busy = (529, {"retry-after": "0"}, _error("overloaded_error", "Overloaded"))
    _fresh(busy, busy, busy)
    try:
        summarize.call_claude_json("Triage this.", summarize.TRIAGE_SCHEMA)
    except RuntimeError as e:
        assert "529" in str(e), e
    else:
        raise AssertionError("an API that stays overloaded must raise")
    assert len(_seen) == 3

def test_unreachable_api_raises():
    _fresh()
    os.environ["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:9"   # discard port: nothing listens
    retries, claude_api.RETRIES = claude_api.RETRIES, 0
    try:
        summarize.call_claude_json("Triage this.", summarize.TRIAGE_SCHEMA)
    except RuntimeError as e:
        assert "unreachable" in str(e), e
    else:
        raise AssertionError("a refused connection must raise")
    finally:
        claude_api.RETRIES = retries


# ── which engine ──────────────────────────────────────────────────────────────
def test_engine_follows_the_key_unless_told_otherwise():
    _fresh()
    assert summarize.engine() == "api" and summarize.model_label() == "claude-opus-5-5"
    os.environ["MAILMIND_AI"] = "cli"
    assert summarize.engine() == "cli" and summarize.model_label() == "claude-cli"
    del os.environ["MAILMIND_AI"]
    os.environ["ANTHROPIC_API_KEY"] = ""
    assert summarize.engine() == "cli"
    os.environ["MAILMIND_AI"] = "api"
    assert summarize.engine() == "api"

def test_a_key_in_the_config_file_wins_over_the_environment():
    _fresh()
    _config(anthropic_api_key="sk-ant-config-key")
    summarize.call_claude_json("Triage this.", summarize.TRIAGE_SCHEMA)
    assert _seen[0][0]["x-api-key"] == "sk-ant-config-key", _seen[0][0]
    assert summarize.describe_engine() == "Claude API · claude-opus-5-5 · key from mailmind.config.json"
    os.environ["MAILMIND_TEST_KEY"] = "sk-ant-env-ref-key"
    _config(anthropic_api_key={"env": "MAILMIND_TEST_KEY"})
    assert claude_api.key() == "sk-ant-env-ref-key"
    del os.environ["MAILMIND_TEST_KEY"]
    _config(anthropic_api_key="")                              # empty is unset: the environment's key
    assert claude_api.key() == "sk-ant-test-key" and claude_api.key_source() == "ANTHROPIC_API_KEY"

def test_a_config_key_that_cannot_be_read_is_no_key():
    _fresh()
    _config(anthropic_api_key={"env": "MAILMIND_TEST_UNSET_KEY"})
    assert claude_api.key() == "" and summarize.engine() == "cli"   # not the environment's key



# ── what the UI is told (summarize.status) ───────────────────────────────────
def _path_with(claude=False):
    """A PATH holding a claude command that exits non-zero as a signed-out one does, or none at all."""
    d = tempfile.mkdtemp()
    if claude:
        with open(os.path.join(d, "claude"), "w") as f:
            f.write("#!/bin/sh\necho 'Invalid API key - Please run /login' >&2\nexit 1\n")
        os.chmod(os.path.join(d, "claude"), 0o755)
    return d

def test_status_names_the_engine_model_and_key_source():
    _fresh()
    st = summarize.status()
    assert (st["engine"], st["model"], st["key_from"]) == ("api", "claude-opus-5-5", "ANTHROPIC_API_KEY"), st

def test_status_records_a_rejected_key_then_a_good_call():
    _fresh((401, {}, _error("authentication_error", "invalid x-api-key")))
    try:
        summarize.call_claude_json("Triage this.", summarize.TRIAGE_SCHEMA)
    except RuntimeError:
        pass
    st = summarize.status()
    assert st["last_ok"] is False and st["last_error"] == "the API key was rejected" and st["last_ts"], st
    assert summarize.call_claude("Say hi.") == '{"summary": "ok"}'
    st = summarize.status()
    assert st["last_ok"] is True and st["last_error"] == "", st

def test_status_without_a_key_follows_the_claude_command():
    _fresh()
    os.environ["ANTHROPIC_API_KEY"] = ""
    path = os.environ["PATH"]
    try:
        os.environ["PATH"] = _path_with(claude=False)
        assert summarize.status()["engine"] == "none" and summarize.status()["model"] == ""
        os.environ["PATH"] = _path_with(claude=True)
        assert (summarize.status()["engine"], summarize.status()["model"]) == ("cli", "claude-cli")
        try:
            summarize.call_claude_json("Triage this.", summarize.TRIAGE_SCHEMA)
        except RuntimeError:
            pass
        assert summarize.status()["last_error"] == "the claude command isn't signed in"
        os.environ["PATH"] = _path_with(claude=False)
        try:
            summarize.call_claude_json("Triage this.", summarize.TRIAGE_SCHEMA)
        except FileNotFoundError:
            pass
        assert summarize.status()["last_error"] == "the claude command isn't installed"
    finally:
        os.environ["PATH"] = path

def test_a_forced_api_engine_without_a_key_is_none():
    _fresh()
    os.environ["ANTHROPIC_API_KEY"] = ""
    os.environ["MAILMIND_AI"] = "api"
    assert summarize.status()["engine"] == "none"


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL", fn.__name__, repr(e))
        except Exception as e:
            failed += 1
            print("ER:U", fn.__name__, repr(e))
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    sys.exit(1 if failed else 0)
