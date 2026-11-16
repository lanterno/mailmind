"""The language model's contract, through the Anthropic SDK, which the server and every command talk through, against the
fake Claude API (fakes/anthropic_api.py), through summarize's call_claude and call_claude_json or the Assistant's gateway:
what a request carries, how replies, refusals and errors come back, when calls are retried, where the key comes from, and
that with no key AI is off and nothing is sent. All addresses are fictional; the key is the fake's.
"""
import json
import socket
import threading
from types import SimpleNamespace

import pytest

from fakes import anthropic_api as fa
from mailmind.assistant.application.ports import AIOff, ModelError
from mailmind.assistant.domain.prompts import SYSTEM, TRIAGE
from mailmind.kernel.assessment import Assessment

ROW = {"pk": 7, "from_name": "Mira Okafor", "from_addr": "mira@example.test",
       "subject": "Saturday market", "date_iso": "2026-10-30T09:00:00+00:00",
       "snippet": "Could you bring the folding table on Saturday? Let me know by Friday."}
NOW_RETRY = {"retry-after-ms": "1"}                        # retry at once


@pytest.fixture
def plain(data_dir, monkeypatch):
    """AI with no model override, structured output on and the key in the environment only (no config file yet). The
    last call's outcome, which report() says, is this test's own: the app's tests take the module's as it was at import."""
    from mailmind.bootstrap import assistant
    monkeypatch.setattr(assistant.status, "_last", {"ok": None, "ts": 0, "error": ""})
    monkeypatch.setattr(assistant.calls, "MODEL", None)
    monkeypatch.delenv("MAILMIND_NO_SCHEMA", raising=False)
    monkeypatch.delenv("MAILMIND_MODEL", raising=False)


@pytest.fixture
def api(plain, ai):
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


def test_an_account_with_no_credit_left_is_named_and_not_a_bad_request(api):
    from mailmind.bootstrap import assistant
    api.queue(fa.error(400, "invalid_request_error", "Your credit balance is too low to access the Anthropic API. "
                                                     "Please go to Plans & Billing to upgrade or purchase credits."))
    with pytest.raises(ModelError) as e:
        assistant.calls.call_claude_json("Triage this.", assistant.assessments.TRIAGE_SCHEMA, feature="thread")
    assert (e.value.no_credit, e.value.permanent, len(api.requests)) == ("Anthropic", True, 1)
    assert assistant.status.report()["last_error"] == "the Anthropic account has no credit left"


# ── how replies come back ─────────────────────────────────────────────────────

def test_triage_stores_the_structured_reply(api):
    from mailmind.bootstrap import assistant
    api.queue(fa.answer({"summary": "Mira asks for the table.", "importance": 9,
                         "category": "action-needed", "action": "Reply by Friday"}))
    assert assistant.assessments.summarize_one(ROW) == (7, Assessment(
        "Mira asks for the table.", 5, "action-needed", "Reply by Friday", "claude-haiku-5-5",
        "claude-opus-5-5", TRIAGE.version, "assessed"))
    assert api.requests[0].body["model"] == "claude-haiku-5-5"


def test_triage_keeps_the_model_a_fallback_handed_over_to(api, monkeypatch):
    from mailmind.bootstrap import assistant
    monkeypatch.setattr(assistant.calls, "MODEL", "claude-opus-5-5")      # Haiku, triage's own, has no fallback
    api.queue(answer({"summary": "Mira asks.", "importance": 3, "category": "info"}, usage={
        "input_tokens": 10, "output_tokens": 5,
        "iterations": [{"type": "fallback_message", "model": "claude-opus-4-8", "input_tokens": 10, "output_tokens": 5}]}))
    _, said = assistant.assessments.summarize_one(ROW)
    assert (said.model, said.served_by, said.outcome) == ("claude-opus-5-5", "claude-opus-4-8", "assessed")


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
    assert (payload.model, payload.outcome) == ("parse-error", "refused")     # stored once, not retried forever
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


@pytest.mark.parametrize("at", [0, 150, 160, 170, 300])          # the key runs past the cut from 155 to 173
def test_no_part_of_the_key_survives_what_the_api_said_cut_to_200_characters(api, at):
    from mailmind.bootstrap import assistant
    words = "x" * at + f"invalid header X-Api-Key: {fa.KEY}; try again " + "y" * 300
    api.queue(fa.error(400, "invalid_request_error", words))
    with pytest.raises(ModelError) as e:
        assistant.calls.call_claude_json("Triage this.", assistant.assessments.TRIAGE_SCHEMA)
    said = str(e.value).removeprefix("Claude API 400: ")
    assert said == words.replace(fa.KEY, "<key>")[:200] and len(said) == 200
    assert not any(fa.KEY[:n] in said for n in range(6, len(fa.KEY) + 1))


def test_what_the_api_said_that_isnt_words_is_kept_as_compact_json(api):
    from mailmind.bootstrap import assistant
    api.queue((400, {"type": "error", "error": {"type": "invalid_request_error", "message": {"code": 401, "why": "café"}}}))
    with pytest.raises(ModelError) as e:
        assistant.calls.call_claude_json("Triage this.", assistant.assessments.TRIAGE_SCHEMA)
    assert (str(e.value), e.value.status) == ('Claude API 400: {"code":401,"why":"café"}', 400)
    assert assistant.status.report()["last_error"] == "the API answered 400"


@pytest.mark.parametrize("body, said", [
    ({"type": "error", "error": {"type": "invalid_request_error", "code": 401}}, ""),    # an error with no message
    ({"type": "error", "error": {"type": "invalid_request_error", "message": 0}}, ""),   # or an empty one
    ({"error": ["bad", 401]}, ""), ({"error": 401}, ""), ({"error": "bad: 401"}, ""),   # an error that is no object...
    ({"error": "bad: 401", "message": "try later"}, "try later"),                       # ...says the body's message
    ("<html><body>401 Bad gateway</body></html>", ""),                                  # and a body that isn't JSON
])
def test_what_the_api_said_is_read_from_its_answer_alone_never_the_sdks_words_for_it(api, body, said):
    from mailmind.bootstrap import assistant
    api.queue((400, body))
    with pytest.raises(ModelError) as e:
        assistant.calls.call_claude_json("Triage this.", assistant.assessments.TRIAGE_SCHEMA)
    assert (str(e.value), e.value.status) == (f"Claude API 400: {said}", 400)
    assert assistant.status.report()["last_error"] == "the API answered 400"
    assert len(api.requests) == 1


def test_a_key_the_api_quotes_in_what_isnt_words_is_taken_out_as_json_writes_it(api, monkeypatch):
    from mailmind.bootstrap import assistant
    key = 'sk-ant-test-"quoted"\\key'
    monkeypatch.setattr(fa, "KEY", key)                     # the fake takes this one
    monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    api.queue((400, {"type": "error", "error": {"type": "invalid_request_error", "message": {"got": key, "why": "bad"}}}))
    with pytest.raises(ModelError) as e:
        assistant.calls.call_claude_json("Triage this.", assistant.assessments.TRIAGE_SCHEMA)
    assert (str(e.value), e.value.status) == ('Claude API 400: {"got":"<key>","why":"bad"}', 400)


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


def test_an_unreachable_api_raises():
    from mailmind.assistant.adapters.anthropic_sdk import AnthropicModel
    from mailmind.assistant.application.ports import ModelError, Prompt
    model = AnthropicModel(fa.KEY, "http://127.0.0.1:9", retries=0)   # discard port
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


def test_a_key_the_config_file_names_wins_over_the_environment(api, data_dir, monkeypatch, fake_keychain):
    from mailmind.bootstrap import assistant
    fake_keychain.add("anthropic-api-key", "sk-ant-config-key", replace=False)
    config(data_dir, anthropic_api_key={"keychain": "anthropic-api-key", "version": 1})
    with pytest.raises(RuntimeError, match="401"):              # the fake knows only its own key
        assistant.calls.call_claude_json("Triage this.", assistant.assessments.TRIAGE_SCHEMA)
    assert api.requests[0].key == "sk-ant-config-key"
    assert assistant.status.describe_engine() == "Claude (Anthropic) · claude-opus-5-5 · key from mailmind.config.json"
    monkeypatch.setenv("MAILMIND_TEST_KEY", "sk-ant-env-ref-key")
    config(data_dir, anthropic_api_key={"env": "MAILMIND_TEST_KEY"})
    assert assistant.key() == ("sk-ant-env-ref-key", "mailmind.config.json")
    config(data_dir, anthropic_api_key="")                      # empty is unset: the environment's key
    assert assistant.key() == (fa.KEY, "ANTHROPIC_API_KEY")
    config(data_dir, anthropic_api_key="sk-ant-in-the-file")    # held in the file: refused, so no key at all
    assert assistant.key() == ("", "mailmind.config.json")


def test_the_startup_line_says_when_there_is_no_key(plain, data_dir, monkeypatch):
    from mailmind.bootstrap import assistant
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    config(data_dir, preferences={"ai_enabled": True})
    assert assistant.status.describe_engine() == ("Claude (Anthropic) · claude-opus-5-5 · no key, so AI is off: "
                                                  "keep one with `uv run python -m mailmind ai key`, or set ANTHROPIC_API_KEY")
    config(data_dir, anthropic_api_key={"env": "MAILMIND_TEST_UNSET_KEY"})
    assert assistant.status.describe_engine() == ("Claude (Anthropic) · claude-opus-5-5 · no key, so AI is off: "
                                                  "the anthropic_api_key in mailmind.config.json can't be read")


def test_with_no_key_anywhere_ai_mode_starts_off(plain, data_dir, monkeypatch):
    from mailmind.bootstrap import assistant
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    st = assistant.status.report()
    assert (st["engine"], st["provider"], st["off"]) == ("none", "anthropic", "turned-off")
    monkeypatch.setenv("ANTHROPIC_API_KEY", fa.KEY)
    assert assistant.status.report()["off"] is None


@pytest.mark.parametrize("preferences, line", [
    ({"ai_enabled": False}, "Claude (Anthropic) · claude-opus-5-5 · AI mode is off: turn it on in Settings, or with "
                            "`uv run python -m mailmind ai on`"),
    ({"ai_provider": "openai"}, "OpenAI · gpt-6.1-sol · no key, so AI is off: keep one in Settings, or with "
                                "`uv run python -m mailmind ai key --provider openai`"),
    ({"ai_provider": "compatible"}, "Another OpenAI-compatible service · no model · no URL, so AI is off: give the service's "
                                    "in Settings"),
    ({"ai_provider": "local"}, "A model on this Mac · no model · no model, so AI is off: choose one in Settings"),
    ({"ai_provider": "local", "ai_model": "llama3.3"}, "A model on this Mac · llama3.3 · no key"),
])
def test_the_startup_line_names_the_provider_and_why_ai_is_off_never_its_url(plain, data_dir, preferences, line):
    from mailmind.bootstrap import assistant
    preferences = {"ai_enabled": True, **preferences}
    config(data_dir, preferences={**preferences, "ai_base_url": "http://127.0.0.1:11500/private-path"}
           if preferences.get("ai_provider") == "local" else preferences)
    assert assistant.status.describe_engine() == line


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


def test_without_a_key_nothing_is_sent_and_nothing_counts_as_failed(plain, anthropic_api):
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


def test_turned_off_nothing_is_sent_whatever_key_there_is(api, data_dir):
    from mailmind.bootstrap import assistant
    config(data_dir, preferences={"ai_enabled": False})
    with pytest.raises(AIOff, match="^AI is off: it is turned off in Settings$"):
        assistant.calls.call_claude_json("Triage this.", assistant.assessments.TRIAGE_SCHEMA)
    assert assistant.assessments.summarize_one(ROW) == (7, None)
    assert api.requests == []
    st = assistant.status.report()
    assert (st["engine"], st["provider"], st["off"], st["model"], st["key_from"]) == ("none", "anthropic", "turned-off", "", "")


def test_the_status_says_why_ai_is_off_and_which_provider_was_chosen(plain, data_dir, monkeypatch):
    from mailmind.bootstrap import assistant
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    config(data_dir, preferences={"ai_enabled": True})
    assert (assistant.status.report()["provider"], assistant.status.report()["off"]) == ("anthropic", "no-key")
    for preferences, said in [({"ai_provider": "gemini"}, ("gemini", "no-key")), ({"ai_provider": "compatible"}, ("compatible", "no-url")),
                              ({"ai_provider": "local"}, ("local", "no-model"))]:
        config(data_dir, preferences={"ai_enabled": True, **preferences})
        st = assistant.status.report()
        assert (st["provider"], st["off"], st["engine"]) == (*said, "none")


# ── the models it lists ───────────────────────────────────────────────────────

def test_the_models_are_listed_with_the_key_through_the_sdk(api):
    from mailmind.assistant.adapters.anthropic_sdk import list_models
    import os
    api.models = ["claude-sonnet-5-5", "claude-opus-5-5"]
    assert list_models(fa.KEY, os.environ["ANTHROPIC_BASE_URL"]) == ["claude-sonnet-5-5", "claude-opus-5-5"]
    [req] = api.requests
    assert (req.method, req.path.split("?")[0], req.key) == ("GET", "/v1/models", fa.KEY)
    assert req.headers["anthropic-version"] == "2026-10-21"


@pytest.mark.parametrize("key", [f"{fa.KEY}\n{fa.KEY}", f"sk-ant {fa.KEY}", f"{fa.KEY}\x00"])
def test_a_key_with_a_blank_or_a_control_character_in_it_is_refused_unsent(api, key):
    import os

    from mailmind.assistant.adapters.anthropic_sdk import AnthropicModel, list_models
    from mailmind.assistant.application.ports import ModelError, Prompt
    model = AnthropicModel(key, os.environ["ANTHROPIC_BASE_URL"])
    for call in (lambda: model.complete(Prompt("Hi.", "claude-opus-5-5", 100)), lambda: model.batch_ended("msgbatch_1"),
                 lambda: model.submit_batch([("1", Prompt("Hi.", "claude-opus-5-5", 100))]),
                 lambda: list_models(key, os.environ["ANTHROPIC_BASE_URL"])):
        with pytest.raises(ModelError) as e:
            call()
        assert (str(e.value), e.value.status, e.value.permanent) == ("Claude API 401: the key was refused", 401, True)
    assert api.requests == []


# ── where a request goes, and nothing the environment says of it ──────────────

@pytest.fixture
def elsewhere():
    """A loopback port no request should reach: the connections made to it, counted, and none answered."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    sock.settimeout(0.05)
    reached, stop = [], threading.Event()

    def accepting():
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
            except TimeoutError:
                continue
            reached.append(conn)
            conn.close()
    thread = threading.Thread(target=accepting, name="elsewhere", daemon=True)
    thread.start()
    yield SimpleNamespace(url=f"http://127.0.0.1:{sock.getsockname()[1]}", reached=reached)
    stop.set()
    thread.join()
    sock.close()


def claude(url, **kw):
    from mailmind.assistant.adapters.anthropic_sdk import AnthropicModel
    return AnthropicModel(fa.KEY, url, **kw)


PROMPT = ("Hi.", "claude-opus-5-5", 100)


def test_a_call_carries_the_apis_headers_and_none_the_environment_names(api, monkeypatch):
    from mailmind.assistant.application.ports import Prompt
    from mailmind.assistant.domain.models import FALLBACK_BETA
    monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "X-Leak: custom\nAuthorization: Bearer sk-ant-elsewhere")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-ant-token-from-the-environment")
    model = claude(api.url)
    model.complete(Prompt(*PROMPT))
    model.complete(Prompt(*PROMPT, fallback=True))
    plain, fallback = api.requests
    assert set(plain.headers) == {"host", "accept-encoding", "content-length", "accept", "content-type",
                                  "anthropic-version", "x-api-key"}
    assert [plain.headers[h] for h in ("accept", "content-type", "anthropic-version", "x-api-key")] == [
        "application/json", "application/json", "2026-10-21", fa.KEY]
    assert set(fallback.headers) - set(plain.headers) == {"anthropic-beta"}
    assert fallback.headers["anthropic-beta"] == FALLBACK_BETA


def test_a_call_goes_straight_to_its_endpoint_whatever_proxy_the_environment_names(api, elsewhere, monkeypatch):
    from mailmind.assistant.adapters.anthropic_sdk import list_models
    from mailmind.assistant.application.ports import Prompt
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        monkeypatch.setenv(name, elsewhere.url)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    claude(api.url, retries=0).complete(Prompt(*PROMPT))
    assert list_models(fa.KEY, api.url) == list(fa.MODELS)
    assert len(api.requests) == 2 and elsewhere.reached == []


def test_a_redirect_is_an_answer_like_any_other_and_never_followed(api, elsewhere):
    from mailmind.assistant.application.ports import Prompt
    api.queue((307, {"type": "error", "error": {"type": "moved", "message": "moved"}},
               {"Location": f"{elsewhere.url}/v1/messages"}))
    with pytest.raises(ModelError) as e:
        claude(api.url, retries=0).complete(Prompt(*PROMPT))
    assert (str(e.value), e.value.status) == ("Claude API 307: moved", 307)
    assert len(api.requests) == 1 and elsewhere.reached == []


UNREACHABLE = "Claude API unreachable: no API address"


@pytest.mark.parametrize("url", [
    "http://llm.example.test", "http://192.0.2.7:8080/v1",                  # the key in the clear off this Mac
    "https://llm.example.test:", "http://127.0.0.1:/", "https://llm.example.test:0", "http://localhost:65536",
    "https://user@llm.example.test", "https://llm.example.test/v1?x=1", "https://llm.example.test/#top",
    "https://[v1.ab]/v1", "https://[fe80::1%25en0]/v1", "ftp://llm.example.test", "https://" + "a" * 500,
    "   ", " https://llm.example.test", "https://llm.example.test/v 1",
])
def test_a_url_a_person_couldnt_give_a_service_is_unreachable_with_nothing_sent(url, monkeypatch):
    """ANTHROPIC_BASE_URL is held to the rules a URL a person gives a service keeps, spaces and all: unreachable before
    anything else is said of a call, a key that can't be one among it, and no connection made."""
    from mailmind.assistant.adapters.anthropic_sdk import list_models
    from mailmind.assistant.application.ports import Prompt
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("connected"))
    model, spaced = claude(url), claude(url, retries=0)
    spaced._key = "sk-ant with-a-space"
    for call in (lambda: model.complete(Prompt(*PROMPT)), lambda: model.submit_batch([("1", Prompt(*PROMPT))]),
                 lambda: model.batch_ended("msgbatch_0001"), lambda: model.batch_results("msgbatch_0001", "claude-opus-5-5"),
                 lambda: model.discard_batch("msgbatch_0001"), lambda: list_models(fa.KEY, url),
                 lambda: spaced.complete(Prompt(*PROMPT)), lambda: list_models("sk-ant with-a-space", url)):
        with pytest.raises(ModelError) as e:
            call()
        assert (str(e.value), e.value.status, e.value.permanent) == (UNREACHABLE, None, False)


def test_with_anthropic_base_url_in_plain_http_off_this_mac_claude_is_unreachable_and_the_key_goes_nowhere(api, monkeypatch):
    from mailmind.bootstrap import assistant
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://llm.example.test")
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("connected"))
    with pytest.raises(RuntimeError, match=f"^{UNREACHABLE}$"):
        assistant.calls.call_claude_json("Triage this.", assistant.assessments.TRIAGE_SCHEMA)
    assert api.requests == []


@pytest.mark.parametrize("url, local", [("http://127.0.0.1:9/", True), ("http://[::1]:9", True),
                                        ("http://127.0.0.1:/", False), ("http://localhost:9/?x=1", False),
                                        ("http://user@127.0.0.1:9/", False), ("http://localhost:0/", False)])
def test_an_anthropic_base_url_on_this_mac_is_so_only_where_a_person_could_give_it(api, monkeypatch, url, local):
    """One that isn't is priced as Claude elsewhere is, and waited for as long."""
    from mailmind.bootstrap import assistant
    monkeypatch.setenv("ANTHROPIC_BASE_URL", url)
    e = assistant.engine()
    assert (e.base_url, e.local, e.timeout) == (url, local, 300 if local else 120)


def test_a_batchs_results_named_on_another_host_are_asked_of_the_endpoint_the_call_was_made_for(api):
    """The API names a batch's results by a URL of its own: one on another host is asked of the endpoint in force, at the
    same path, and the key never goes there."""
    from mailmind.assistant.application.ports import Prompt
    model = claude(api.url)
    batch_id = model.submit_batch([("1", Prompt(*PROMPT))])
    api.end_batch(batch_id)
    api.queue(lambda req: (200, {**api._view(batch_id),
                                 "results_url": f"https://batches.example.test{fa.BATCHES}/{batch_id}/results"}))
    [result] = model.batch_results(batch_id, "claude-opus-5-5")
    assert result.custom_id == "1" and result.reply is not None
    asked = api.requests[-1]
    assert (asked.method, asked.path, asked.headers["accept"], asked.key) == (
        "GET", f"{fa.BATCHES}/{batch_id}/results", "application/binary", fa.KEY)


# ── what the app talks through ────────────────────────────────────────────────

def test_a_process_talks_through_the_sdk_without_front_build(api):
    """The command line never comes through front.build, and reaches the API the way the server does."""
    from mailmind.assistant.adapters.anthropic_sdk import AnthropicModel
    from mailmind.bootstrap import assistant
    assert isinstance(assistant.gateway()._model(), AnthropicModel)


def test_the_model_is_made_once_per_key(api, monkeypatch):
    from mailmind.bootstrap import assistant
    gateway = assistant.gateway()
    first = gateway._model()
    assert gateway._model() is first
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-another-key")
    assert gateway._model() is not first and gateway._model().enabled
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert gateway._model().enabled is False
