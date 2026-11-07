"""The language model's contract, through each transport the app runs: the Anthropic SDK (the new
front door) and the urllib client (the old one, with nothing installed). Each test runs once per
transport against the fake Claude API (fakes/anthropic_api.py), through summarize's call_claude and
call_claude_json or the Assistant's gateway: what a request carries, how replies, refusals and
errors come back, when calls are retried, where the key comes from, and that with no key AI is off
and nothing is sent. All addresses are fictional; the key is the fake's.

Ported from test_claude_api.py, the root suite that tested the urllib client alone.
"""
import json

import pytest

from fakes import anthropic_api as fa
from mailmind.assistant.application.ports import AIOff
from mailmind.assistant.domain.prompts import SYSTEM

TRANSPORTS = ["sdk", "urllib"]

ROW = {"pk": 7, "from_name": "Mira Okafor", "from_addr": "mira@example.test",
       "subject": "Saturday market", "date_iso": "2026-10-30T09:00:00+00:00",
       "snippet": "Could you bring the folding table on Saturday? Let me know by Friday."}
NOW_RETRY = {"retry-after": "0", "retry-after-ms": "1"}    # retry at once, for either client


@pytest.fixture(params=TRANSPORTS)
def transport(request, data_dir, monkeypatch):
    """AI through this transport, with no model override, structured output on and the key in
    the environment only (no config file yet). The last call's outcome, which report() says,
    is this test's own: the app's tests take the module's as it was at import."""
    from mailmind.bootstrap import assistant
    monkeypatch.setattr(assistant.status, "_last", {"ok": None, "ts": 0, "error": ""})
    monkeypatch.setattr(assistant.calls, "MODEL", None)
    monkeypatch.delenv("MAILMIND_NO_SCHEMA", raising=False)
    monkeypatch.delenv("MAILMIND_MODEL", raising=False)
    assistant.use(request.param)
    yield request.param
    assistant.use("urllib")


@pytest.fixture
def api(transport, ai):
    return ai


def config(data_dir, **top):
    with open(data_dir / "mailmind.config.json", "w") as f:
        json.dump({"accounts": [], **top}, f)


def answer(obj, **extra):
    status, body = fa.answer(obj)
    return status, {**body, **extra}


# ── what a request carries ────────────────────────────────────────────────────

def test_a_request_carries_the_key_the_version_and_the_refusal_fallback(api):
    from mailmind.bootstrap import assistant
    assistant.calls.call_claude_json("Triage this.", assistant.assessments.TRIAGE_SCHEMA)
    [req] = api.requests
    assert req.key == fa.KEY
    assert req.headers["anthropic-version"] == "2026-10-21"
    assert req.headers["anthropic-beta"] == "server-side-fallback-2026-07-01"
    assert req.body["model"] == "claude-opus-5-5" and req.body["fallbacks"] == "default"
    assert req.body["messages"] == [{"role": "user", "content": "Triage this."}]
    assert not {"temperature", "top_p", "top_k", "thinking"} & set(req.body)


def test_every_call_goes_under_the_system_prompt_with_the_mail_in_the_user_turn(api, monkeypatch):
    from mailmind.bootstrap import assistant
    assistant.assessments.summarize_one(ROW)                                        # first: it yields to the others
    assistant.calls.call_claude("Say hello.")                                       # beta: it takes the fallback
    assistant.calls.call_claude_json("Triage this.", assistant.assessments.TRIAGE_SCHEMA)
    monkeypatch.setattr(assistant.calls, "MODEL", "claude-haiku-5-5")               # plain: it doesn't
    assistant.calls.call_claude_json("Triage this.", assistant.assessments.TRIAGE_SCHEMA)
    triage, plain, schema, no_fallback = api.requests
    assert [r.system for r in api.requests] == [SYSTEM] * 4
    assert "fallbacks" in plain.body and "fallbacks" not in no_fallback.body
    assert plain.prompt == "Say hello." and schema.prompt == "Triage this."
    assert "Saturday market" in triage.prompt and "Saturday market" not in triage.system


def test_the_schema_goes_out_closed_and_without_bounds(api):
    from mailmind.bootstrap import assistant
    assistant.calls.call_claude_json("Triage this.", assistant.assessments.TRIAGE_SCHEMA)
    fmt = api.requests[0].body["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    sent = fmt["schema"]
    assert sent["additionalProperties"] is False and sent["required"] == ["summary", "importance", "category"]
    assert sent["properties"]["importance"] == {"type": "integer"}
    assert sent["properties"]["category"]["enum"][0] == "action-needed"
    assert assistant.assessments.TRIAGE_SCHEMA["properties"]["importance"]["maximum"] == 5   # the original is untouched


def test_nested_objects_are_closed_and_property_names_kept():
    from mailmind.assistant.domain.schema import structured
    nested = {"type": "object", "properties": {
        "maximum": {"type": "string", "maxLength": 10},                 # a property that shares a keyword's name
        "groups": {"type": "array", "items": {"type": "object", "properties": {"n": {"type": "integer", "minimum": 0}}}}}}
    out = structured(nested)
    assert out["properties"]["maximum"] == {"type": "string"}
    item = out["properties"]["groups"]["items"]
    assert item["additionalProperties"] is False and item["properties"]["n"] == {"type": "integer"}


def test_triage_runs_at_low_effort_and_the_rest_at_medium(api):
    from mailmind.bootstrap import assistant
    api.queue(fa.answer({"summary": "Mira asks for the table.", "importance": 4, "category": "personal"}))
    assistant.assessments.summarize_one(ROW)
    assistant.calls.call_claude_json("Draft a reply.", {"type": "object", "properties": {"body": {"type": "string"}}})
    assert [r.body["output_config"]["effort"] for r in api.requests] == ["low", "medium"]


def test_a_model_override_and_the_models_without_effort_or_fallback(api, monkeypatch):
    from mailmind.bootstrap import assistant
    monkeypatch.setattr(assistant.calls, "MODEL", "claude-haiku-4-5")
    assistant.calls.call_claude_json("Triage this.", assistant.assessments.TRIAGE_SCHEMA)
    monkeypatch.setattr(assistant.calls, "MODEL", "claude-haiku-5-5")
    assistant.calls.call_claude_json("Triage this.", assistant.assessments.TRIAGE_SCHEMA)
    old, new = api.requests
    assert old.body["model"] == "claude-haiku-4-5" and "effort" not in old.body["output_config"]
    assert new.body["model"] == "claude-haiku-5-5" and new.body["output_config"]["effort"] == "medium"
    assert "fallbacks" not in old.body and "fallbacks" not in new.body
    assert "anthropic-beta" not in old.headers and "anthropic-beta" not in new.headers
    assert assistant.calls.model_label() == "claude-haiku-5-5"


def test_without_schemas_no_format_goes_out_and_loose_json_is_read(api, monkeypatch):
    from mailmind.bootstrap import assistant
    api.queue(fa.answer('Here you go: {"summary": "loose", "importance": 2, "category": "info"}'))
    monkeypatch.setenv("MAILMIND_NO_SCHEMA", "1")
    assert assistant.calls.call_claude_json("Triage this.", assistant.assessments.TRIAGE_SCHEMA)["summary"] == "loose"
    assert "format" not in api.requests[0].body["output_config"]


# ── how replies come back ─────────────────────────────────────────────────────

def test_triage_stores_the_structured_reply(api):
    from mailmind.bootstrap import assistant
    api.queue(fa.answer({"summary": "Mira asks for the table.", "importance": 9,
                         "category": "action-needed", "action": "Reply by Friday"}))
    assert assistant.assessments.summarize_one(ROW) == (7, ("Mira asks for the table.", 5, "action-needed",
                                                "Reply by Friday", "claude-opus-5-5"))


def test_a_plain_text_call_returns_the_text(api):
    from mailmind.bootstrap import assistant
    api.queue(fa.answer("  The thread is about a market stall.  "))
    assert assistant.calls.call_claude("Summarize this thread.") == "The thread is about a market stall."
    body = api.requests[0].body
    assert "output_config" in body and "format" not in body["output_config"]


def test_a_refusal_reads_as_an_empty_reply(api):
    from mailmind.bootstrap import assistant
    api.queue((200, {"stop_reason": "refusal", "content": [],
                     "stop_details": {"type": "refusal", "category": "cyber"}}),
              (200, {"stop_reason": "refusal", "content": []}))
    _, payload = assistant.assessments.summarize_one(ROW)       # triage first: it yields for 45s after any other call
    assert payload is not None and payload[-1] == "parse-error"   # stored once, not retried forever
    assert assistant.calls.call_claude_json("Triage this.", assistant.assessments.TRIAGE_SCHEMA) == {}


def test_only_the_answer_after_a_fallback_block_counts(api):
    from mailmind.bootstrap import assistant
    api.queue((200, {"type": "message", "model": "claude-opus-5-5", "stop_reason": "end_turn", "content": [
        {"type": "text", "text": '{"summary": "half'},
        {"type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "claude-opus-4-8"},
         "trigger": {"type": "refusal"}},
        {"type": "text", "text": '{"summary": "whole", "importance": 3, "category": "info"}'}]}))
    assert assistant.calls.call_claude_json("Triage this.", assistant.assessments.TRIAGE_SCHEMA)["summary"] == "whole"


def test_a_reply_names_the_model_that_served_it_and_what_it_used(api):
    from mailmind.bootstrap import assistant
    api.queue(answer("Lunch moved to Thursday.", usage={
        "input_tokens": 1200, "output_tokens": 80, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}))
    reply = assistant.gateway().ask("Catch me up.", feature="thread")
    assert (reply.text, reply.model, reply.stop_reason) == ("Lunch moved to Thursday.", "claude-opus-5-5", "end_turn")
    assert (reply.usage.input_tokens, reply.usage.output_tokens) == (1200, 80)
    api.queue(answer("Lunch moved to Thursday.", usage={
        "input_tokens": 1200, "output_tokens": 95, "cache_creation_input_tokens": 300, "cache_read_input_tokens": 900,
        "iterations": [
            {"type": "message", "input_tokens": 600, "output_tokens": 15,
             "cache_creation_input_tokens": 300, "cache_read_input_tokens": 0},
            {"type": "fallback_message", "model": "claude-opus-4-8", "input_tokens": 600, "output_tokens": 80,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 900}]}))
    reply = assistant.gateway().ask("Catch me up.", feature="thread")
    assert reply.model == "claude-opus-4-8"                        # the fallback served it
    assert (reply.usage.cache_write_tokens, reply.usage.cache_read_tokens) == (300, 900)


# ── errors and retries ────────────────────────────────────────────────────────

def test_a_rate_limit_is_retried(api):
    from mailmind.bootstrap import assistant
    api.queue(fa.error(429, "rate_limit_error", "slow down", NOW_RETRY))
    assert assistant.calls.call_claude_json("Triage this.", assistant.assessments.TRIAGE_SCHEMA)["summary"] == "stub summary"
    assert len(api.requests) == 2


def test_a_bad_key_raises_without_a_retry_and_without_the_key(api):
    from mailmind.bootstrap import assistant
    api.queue(fa.error(401, "authentication_error", "invalid x-api-key"))
    with pytest.raises(RuntimeError) as e:
        assistant.calls.call_claude_json("Triage this.", assistant.assessments.TRIAGE_SCHEMA)
    assert "401" in str(e.value) and "invalid x-api-key" in str(e.value) and "sk-ant" not in str(e.value)
    assert e.value.status == 401 and e.value.permanent
    assert len(api.requests) == 1


def test_a_failed_triage_is_left_for_the_next_run(api):
    from mailmind.bootstrap import assistant
    api.queue(fa.error(400, "invalid_request_error", "bad"))
    assert assistant.assessments.summarize_one(ROW) == (7, None)


def test_a_persistent_overload_gives_up_after_two_retries(api):
    from mailmind.bootstrap import assistant
    busy = fa.error(529, "overloaded_error", "Overloaded", NOW_RETRY)
    api.queue(busy, busy, busy)
    with pytest.raises(RuntimeError, match="529") as e:
        assistant.calls.call_claude_json("Triage this.", assistant.assessments.TRIAGE_SCHEMA)
    assert not e.value.permanent
    assert len(api.requests) == 3


def test_an_unreachable_api_raises(transport):
    from mailmind.assistant.application.ports import ModelError, Prompt
    from mailmind.bootstrap import assistant
    model = assistant.TRANSPORTS[transport](fa.KEY, "http://127.0.0.1:9", retries=0)   # discard port
    with pytest.raises(ModelError, match="unreachable") as e:
        model.complete(Prompt("Triage this.", "claude-opus-5-5", 100))
    assert e.value.status is None and not e.value.permanent


# ── the key, and AI on or off ─────────────────────────────────────────────────

def test_ai_is_on_with_a_key_and_off_without_one(api, monkeypatch):
    from mailmind.bootstrap import assistant
    assert assistant.calls.engine() == "api" and assistant.calls.model_label() == "claude-opus-5-5"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert assistant.calls.engine() == "none"
    monkeypatch.setenv("MAILMIND_AI", "api")                     # no longer read: there is one engine
    assert assistant.calls.engine() == "none"


def test_a_key_in_the_config_file_wins_over_the_environment(api, data_dir, monkeypatch, transport):
    from mailmind.bootstrap import assistant
    config(data_dir, anthropic_api_key="sk-ant-config-key")
    with pytest.raises(RuntimeError, match="401"):              # the fake knows only its own key
        assistant.calls.call_claude_json("Triage this.", assistant.assessments.TRIAGE_SCHEMA)
    assert api.requests[0].key == "sk-ant-config-key"
    via = {"sdk": "the Anthropic SDK", "urllib": "urllib"}[transport]
    assert assistant.status.describe_engine() == f"Claude API through {via} · claude-opus-5-5 · key from mailmind.config.json"
    monkeypatch.setenv("MAILMIND_TEST_KEY", "sk-ant-env-ref-key")
    config(data_dir, anthropic_api_key={"env": "MAILMIND_TEST_KEY"})
    assert assistant.key() == ("sk-ant-env-ref-key", "mailmind.config.json")
    config(data_dir, anthropic_api_key="")                      # empty is unset: the environment's key
    assert assistant.key() == (fa.KEY, "ANTHROPIC_API_KEY")


def test_the_startup_line_says_when_there_is_no_key(transport, data_dir, monkeypatch):
    from mailmind.bootstrap import assistant
    via = {"sdk": "the Anthropic SDK", "urllib": "urllib"}[transport]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert assistant.status.describe_engine() == (f"Claude API through {via} · claude-opus-5-5 · no key, so AI is off: "
                                           "set anthropic_api_key in mailmind.config.json, or ANTHROPIC_API_KEY")
    config(data_dir, anthropic_api_key={"env": "MAILMIND_TEST_UNSET_KEY"})
    assert assistant.status.describe_engine() == (f"Claude API through {via} · claude-opus-5-5 · no key, so AI is off: "
                                           "the anthropic_api_key in mailmind.config.json can't be read")


def test_a_config_key_that_cannot_be_read_is_no_key(api, data_dir):
    from mailmind.bootstrap import assistant
    config(data_dir, anthropic_api_key={"env": "MAILMIND_TEST_UNSET_KEY"})
    assert assistant.key() == ("", "mailmind.config.json") and assistant.calls.engine() == "none"   # not the environment's


# ── what the UI is told (assistant.status.report) ───────────────────────────────────

def test_status_names_the_engine_the_model_and_the_key_source(api):
    from mailmind.bootstrap import assistant
    st = assistant.status.report()
    assert (st["engine"], st["model"], st["key_from"]) == ("api", "claude-opus-5-5", "ANTHROPIC_API_KEY")


def test_status_records_a_rejected_key_then_a_good_call(api):
    from mailmind.bootstrap import assistant
    api.queue(fa.error(401, "authentication_error", "invalid x-api-key"))
    with pytest.raises(RuntimeError):
        assistant.calls.call_claude_json("Triage this.", assistant.assessments.TRIAGE_SCHEMA)
    st = assistant.status.report()
    assert st["last_ok"] is False and st["last_error"] == "the API key was rejected" and st["last_ts"]
    assert assistant.calls.call_claude("Say hi.") == fa.STUB_TEXT
    st = assistant.status.report()
    assert st["last_ok"] is True and st["last_error"] == ""


def test_without_a_key_nothing_is_sent_and_nothing_counts_as_failed(transport, anthropic_api):
    from mailmind.bootstrap import assistant
    before = assistant.status.report()
    assert (before["engine"], before["model"], before["key_from"]) == ("none", "", "")
    with pytest.raises(AIOff):
        assistant.calls.call_claude_json("Triage this.", assistant.assessments.TRIAGE_SCHEMA)
    with pytest.raises(AIOff):
        assistant.calls.call_claude("Summarize this thread.")
    assert anthropic_api.requests == []
    after = assistant.status.report()
    assert (after["last_ok"], after["last_ts"], after["last_error"]) == \
        (before["last_ok"], before["last_ts"], before["last_error"])
    assert assistant.assessments.summarize_one(ROW) == (7, None)            # left for when AI is on, not stored


# ── which door talks through which ────────────────────────────────────────────

def test_the_new_front_door_talks_through_the_sdk():
    from mailmind.bootstrap import assistant, front
    assistant.use("urllib")
    try:
        front.build()
        assert assistant.transport() == "the Anthropic SDK"
    finally:
        assistant.use("urllib")


def test_the_model_is_made_once_per_key(api, monkeypatch):
    from mailmind.bootstrap import assistant
    gateway = assistant.gateway()
    first = gateway._model()
    assert gateway._model() is first
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-another-key")
    assert gateway._model() is not first and gateway._model().enabled
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert gateway._model().enabled is False
