"""The OpenAI-compatible adapter's contract (#49), the language model for every provider but Claude, against the fake
OpenAI-compatible API (fakes/openai_api.py), through the Assistant's calls and gateway as the app makes them, with the provider,
model and URL chosen in the preferences and the key kept in the Keychain's stand-in: what a request carries for each provider,
how replies, refusals and errors come back, when a call is tried again, a service that won't take structured output, the models
a provider lists, and that the usage ledger, the cost governor and the status see these calls as Claude's. All addresses are
fictional, and the key is the fake's.
"""
import json
import socket

import pytest

from fakes import openai_api as fo
from mailmind.assistant.adapters.openai_chat import ChatModel, list_models
from mailmind.assistant.application.ports import AIOff, ModelError, NoBatches, Prompt
from mailmind.assistant.domain.prompts import SYSTEM

ROW = {"pk": 7, "message_key": 1007, "from_name": "Mira Okafor", "from_addr": "mira@example.test",
       "subject": "Saturday market", "date_iso": "2026-10-30T09:00:00+00:00",
       "snippet": "Could you bring the folding table on Saturday? Let me know by Friday."}
NOW_RETRY = {"retry-after-ms": "1"}                        # try again at once
SCHEMA = {"type": "object", "properties": {"body": {"type": "string"}}, "required": ["body"]}


@pytest.fixture
def plain(data_dir, monkeypatch):
    """No model override and structured output on; the last call's outcome this test's own."""
    from mailmind.bootstrap import assistant
    monkeypatch.setattr(assistant.status, "_last", {"ok": None, "ts": 0, "error": ""})
    monkeypatch.setattr(assistant.calls, "MODEL", None)
    monkeypatch.delenv("MAILMIND_NO_SCHEMA", raising=False)
    monkeypatch.delenv("MAILMIND_MODEL", raising=False)


@pytest.fixture
def choose(plain, data_dir, fake_keychain, openai_api):
    """Choose `provider` in the preferences, reached at the fake, with its key kept as Settings keeps it (unless `key` is
    None), and any other preference given."""
    def choosing(provider, key=fo.KEY, **more):
        top = {}
        if key is not None:
            fake_keychain.add(f"{provider}-api-key", key, replace=True)
            top[f"{provider}_api_key"] = {"keychain": f"{provider}-api-key", "version": 1}
        prefs = {"ai_enabled": True, "ai_provider": provider, "ai_base_url": openai_api.url, **more}
        (data_dir / "mailmind.config.json").write_text(json.dumps({"accounts": [], "preferences": prefs, **top}))
        return openai_api
    return choosing


def ask(schema=SCHEMA, text="Draft a reply.", feature="draft"):
    from mailmind.bootstrap import assistant
    return assistant.calls.call_claude_json(text, schema, feature=feature)


# ── what a request carries ────────────────────────────────────────────────────

def test_a_request_goes_to_chat_completions_with_the_key_the_system_prompt_and_the_schema(choose):
    api = choose("openai")
    assert ask() == {"body": "stub body"}
    [req] = api.requests
    assert (req.method, req.path, req.key) == ("POST", "/chat/completions", fo.KEY)
    assert req.body["model"] == "gpt-6.1-sol" and req.body["max_completion_tokens"] == 16000
    assert req.body["messages"] == [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "Draft a reply."}]
    assert req.body["response_format"] == {"type": "json_schema", "json_schema": {
        "name": "answer", "strict": True,
        "schema": {"type": "object", "properties": {"body": {"type": "string"}}, "required": ["body"],
                   "additionalProperties": False}}}
    assert not {"max_tokens", "output_config", "effort", "fallbacks", "temperature", "reasoning_effort"} & set(req.body)
    assert not [h for h in req.headers if h.startswith("anthropic") or h == "x-api-key"]


def test_a_schema_that_leaves_a_property_out_goes_without_strict(choose):
    from mailmind.bootstrap import assistant
    api = choose("openai")
    assistant.assessments.summarize_one(ROW)
    assert api.requests[0].body["response_format"]["json_schema"]["strict"] is False
    assert "Saturday market" in api.requests[0].prompt and "Saturday market" not in api.requests[0].system


def test_gemini_asks_max_tokens_of_its_model(choose):
    api = choose("gemini")
    ask()
    [req] = api.requests
    assert req.body["model"] == "gemini-4-hydrogen" and req.body["max_tokens"] == 16000
    assert "max_completion_tokens" not in req.body


@pytest.mark.parametrize("provider, model", [("compatible", "mixtral-9x"), ("local", "llama3.3")])
def test_a_model_on_this_mac_and_a_compatible_service_are_asked_with_no_ceiling(choose, provider, model):
    api = choose(provider, ai_model=model)
    ask()
    [req] = api.requests
    assert req.body["model"] == model and not {"max_tokens", "max_completion_tokens"} & set(req.body)


def test_gemini_asks_its_own_model_unless_another_is_chosen(choose):
    api = choose("gemini")
    ask()
    assert api.requests[-1].body["model"] == "gemini-4-hydrogen"


def test_a_model_on_this_mac_is_sent_its_key_only_when_one_is_kept(choose):
    api = choose("local", key=None, ai_model="llama3.3")
    api.key = None
    ask()
    assert "authorization" not in api.requests[-1].headers
    choose("local", ai_model="llama3.3")
    ask()
    assert api.requests[-1].key == fo.KEY


def test_a_compatible_service_is_sent_its_key_only_when_one_is_kept(choose):
    api = choose("compatible", key=None, ai_model="mixtral-9x")
    api.key = None
    ask()
    assert "authorization" not in api.requests[-1].headers
    choose("compatible", ai_model="mixtral-9x")
    ask()
    assert api.requests[-1].key == fo.KEY


def test_the_claude_model_mailmind_model_names_is_ignored_with_another_provider(choose, monkeypatch):
    from mailmind.bootstrap import assistant
    monkeypatch.setattr(assistant.calls, "MODEL", "claude-haiku-5-5")
    api = choose("openai", ai_model="gpt-6-luna")
    ask()
    assert api.requests[-1].body["model"] == "gpt-6-luna" and assistant.calls.model_label() == "gpt-6-luna"
    assert assistant.status.report()["model"] == "gpt-6-luna"
    assistant.assessments.summarize_one({"pk": 1, "from_name": "", "from_addr": "ada@example.test", "subject": "Lunch",
                                         "date_iso": "", "snippet": "Thursday?"})
    assert api.requests[-1].body["model"] == "gpt-6-luna"
    choose("openai")
    ask()
    assert api.requests[-1].body["model"] == "gpt-6.1-sol"


# ── how replies come back ─────────────────────────────────────────────────────

def test_a_reply_names_the_model_that_served_it_and_what_it_used(choose):
    from mailmind.bootstrap import assistant
    api = choose("openai")
    api.queue(fo.answer("Lunch moved to Thursday.", model="gpt-6.1-sol-2026-10-01", prompt_tokens=1200, completion_tokens=80,
                        prompt_tokens_details={"cached_tokens": 1000}))
    reply = assistant.gateway().ask("Catch me up.", feature="thread")
    assert (reply.text, reply.model, reply.stop_reason) == ("Lunch moved to Thursday.", "gpt-6.1-sol-2026-10-01", "stop")
    assert (reply.usage.input_tokens, reply.usage.output_tokens, reply.usage.cache_read_tokens,
            reply.usage.cache_write_tokens) == (200, 80, 1000, 0)


def test_a_refusal_reads_as_an_empty_reply_and_triage_stores_it_once(choose):
    from mailmind.bootstrap import assistant
    api = choose("openai")
    api.queue(fo.refusal(), fo.refusal())
    _, said = assistant.assessments.summarize_one(ROW)
    assert (said.model, said.served_by, said.outcome) == ("parse-error", "gpt-6.1-sol", "refused")
    assert ask() == {}


def test_triage_stores_the_structured_reply_under_the_model_in_force(choose):
    from mailmind.bootstrap import assistant
    api = choose("openai", ai_model="gpt-6-luna")
    api.queue(fo.answer({"summary": "Mira asks for the table.", "importance": 9, "category": "action-needed",
                         "action": "Reply by Friday"}, model="gpt-6-luna"))
    _, said = assistant.assessments.summarize_one(ROW)
    assert (said.summary, said.importance, said.model, said.served_by, said.outcome) == (
        "Mira asks for the table.", 5, "gpt-6-luna", "gpt-6-luna", "assessed")


# ── a service without structured output ───────────────────────────────────────

@pytest.mark.parametrize("provider", ["local", "compatible"])
def test_a_service_that_refuses_response_format_is_asked_once_more_without_it(choose, provider):
    api = choose(provider, key=None, ai_model="llama3.3")
    api.key, api.refuse_format = None, True
    api.queue(fo.error(400, "response_format of type json_schema is not supported"),
              fo.answer('Here it is: {"body": "See you Saturday."} Anything else?'))
    assert ask() == {"body": "See you Saturday."}
    first, second = api.chats
    assert "response_format" in first.body and "response_format" not in second.body
    schema = first.body["response_format"]["json_schema"]["schema"]
    assert second.body["messages"] == [first.body["messages"][0], {"role": "user", "content": (
        "Draft a reply.\n\nReply with only a JSON object that matches this JSON Schema: "
        + json.dumps(schema, separators=(",", ":")))}]


def test_rewrite_still_answers_from_a_service_that_answers_prose_unless_the_prompt_asks_for_json(choose):
    from mailmind.bootstrap import assistant
    api = choose("compatible", key=None, ai_model="mixtral-9x")
    api.key, api.refuse_format = None, True

    def answering(req):
        asked = "Reply with only a JSON object that matches this JSON Schema: " in req.prompt
        return fo.answer('{"text": "See you on Thursday."}' if asked else "See you on Thursday.")
    api.queue(fo.error(400, "This response_format type is unavailable now"), answering)
    assert assistant.rewriter.rewrite("see you thursday", "shorten")["text"] == "See you on Thursday."
    assert len(api.chats) == 2


def test_a_reasoning_models_thinking_is_dropped_before_its_reply_is_read(choose):
    api = choose("local", key=None, ai_model="qwen3:8b")
    api.key, api.refuse_format = None, True
    api.queue(fo.error(400, "response_format of type json_schema is not supported"),
              fo.answer('<think>They want {"body": ...}, so a short yes.</think>\n{"body": "See you Saturday."}'))
    assert ask() == {"body": "See you Saturday."}


def test_openai_is_not_asked_again_without_it(choose):
    api = choose("openai")
    api.queue(fo.error(400, "Invalid schema for response_format 'answer'"))
    with pytest.raises(ModelError) as e:
        ask()
    assert e.value.status == 400 and e.value.permanent and len(api.requests) == 1


def test_a_400_about_something_else_is_not_asked_again(choose):
    api = choose("compatible", key=None, ai_model="mixtral-9x")
    api.key = None
    api.queue(fo.error(400, "context length exceeded"))
    with pytest.raises(ModelError, match="400"):
        ask()
    assert len(api.requests) == 1


# ── a ceiling a model can't meet ──────────────────────────────────────────────

@pytest.mark.parametrize("provider, ceiling, words", [
    ("openai", "max_completion_tokens", "max_completion_tokens is too large: 16000. This model supports at most 4096."),
    ("openai", "max_completion_tokens", "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead."),
    ("gemini", "max_tokens", "Invalid max_tokens value, the valid range of max_tokens is [1, 8192]"),
])
def test_a_ceiling_refused_is_asked_once_more_without_it(choose, provider, ceiling, words):
    api = choose(provider)
    api.queue(fo.error(400, words))
    assert ask() == {"body": "stub body"}
    first, second = api.chats
    assert first.body[ceiling] == 16000 and not {"max_tokens", "max_completion_tokens"} & set(second.body)
    assert {k: v for k, v in first.body.items() if k != ceiling} == second.body


def test_a_ceiling_refused_is_asked_again_only_once(choose):
    api = choose("openai")
    refused = fo.error(400, "max_completion_tokens is too large: 16000.")
    api.queue(refused, refused, refused)
    with pytest.raises(ModelError) as e:
        ask()
    assert e.value.status == 400 and len(api.chats) == 2


def test_a_ceiling_gemini_refuses_inside_its_list_is_asked_once_more_without_it(choose):
    api = choose("gemini")
    api.queue(fo.gemini_error(400, "Invalid max_tokens value, the valid range of max_tokens is [1, 8192]"))
    assert ask() == {"body": "stub body"}
    first, second = api.chats
    assert first.body["max_tokens"] == 16000 and "max_tokens" not in second.body


@pytest.mark.parametrize("refusal", [
    (400, {"error": {"message": "Unsupported parameter.", "type": "invalid_request_error", "param": "max_tokens"}}),
    (400, {"error": {"type": "invalid_request_error", "detail": "max_completion_tokens is too large"}}),
    (400, "max_completion_tokens is too large"),
], ids=["its-param", "no-message", "no-error"])
def test_a_400_that_names_the_ceiling_only_outside_what_the_service_said_is_not_asked_again(choose, refusal):
    api = choose("openai")
    api.queue(refusal)
    with pytest.raises(ModelError) as e:
        ask()
    assert e.value.status == 400 and len(api.chats) == 1


def test_a_400_naming_the_ceiling_from_a_service_given_none_is_not_asked_again(choose):
    api = choose("compatible", key=None, ai_model="mixtral-9x")
    api.key = None
    api.queue(fo.error(400, "max_tokens must be at least 1"))
    with pytest.raises(ModelError, match="400"):
        ask()
    assert len(api.requests) == 1


def test_a_service_without_structured_output_is_asked_again_only_once_for_it(choose):
    api = choose("local", key=None, ai_model="llama3.3")
    api.key = None
    refused = fo.error(400, "response_format of type json_schema is not supported")
    api.queue(refused, refused, refused)
    with pytest.raises(ModelError) as e:
        ask()
    assert e.value.status == 400 and len(api.chats) == 2


# ── errors and retries ────────────────────────────────────────────────────────

def test_a_rejected_key_raises_without_a_retry_and_without_any_of_the_key(choose):
    from mailmind.bootstrap import assistant
    api = choose("openai", key="sk-test-wrong-key-abcdefghijkl")
    with pytest.raises(ModelError) as e:
        ask()
    assert e.value.status == 401 and e.value.permanent and len(api.requests) == 1
    assert "wrong" not in str(e.value) and "ijkl" not in str(e.value) and "sk-te" not in str(e.value)
    assert assistant.status.report()["last_error"] == "the API key was rejected"


@pytest.mark.parametrize("refusal", [
    fo.gemini_error(400, "API key not valid. Please pass a valid API key.", "API_KEY_INVALID"),
    fo.gemini_error(400, "The key given is no key.", "API_KEY_INVALID"),                 # the reason alone
    fo.gemini_error(400, "API key not valid. Please pass a valid API key."),             # the words alone
    (400, {"error": {"message": "API key not valid.", "details": [{"reason": "API_KEY_INVALID"}]}}),   # not in a list
])
def test_a_key_gemini_refuses_with_a_400_is_a_refused_key(choose, refusal):
    from mailmind.bootstrap import assistant
    api = choose("gemini", key="sk-test-wrong-key-abcdefghijkl")
    api.key = None
    api.queue(refusal)
    with pytest.raises(ModelError) as e:
        ask()
    assert (str(e.value), e.value.status, e.value.permanent) == ("gemini API 401: the key was refused", 401, True)
    assert len(api.requests) == 1
    assert assistant.status.report()["last_error"] == "the API key was rejected"


def test_a_key_refused_with_a_400_stops_every_background_call_as_a_401_does(choose):
    from mailmind.bootstrap import assistant
    api = choose("gemini", key="sk-test-wrong-key-abcdefghijkl")
    api.key = None
    api.queue(fo.gemini_error(400, "API key not valid. Please pass a valid API key.", "API_KEY_INVALID"))
    with pytest.raises(ModelError):
        ask()
    assert assistant.governor()._is_open("triage", "gemini-4-hydrogen", assistant.gateway()._model())


@pytest.mark.parametrize("refusal", [
    fo.gemini_error(400, "Request contains an invalid argument.", "BAD_REQUEST"),
    fo.gemini_error(400, "The key's project has no billing.", kind="FAILED_PRECONDITION"),
    (400, {"error": {"message": "Invalid value: API key not valid is no reason."}}),      # the words, not first
    (422, [{"error": {"message": "API key not valid.", "details": [{"reason": "API_KEY_INVALID"}]}}]),    # not a 400
])
def test_any_other_refusal_is_no_refused_key_and_says_what_the_service_said_from_inside_a_list(choose, refusal):
    api = choose("gemini")
    api.queue(refusal)
    with pytest.raises(ModelError) as e:
        ask()
    status, body = refusal
    said = (body[0] if isinstance(body, list) else body)["error"]["message"]
    assert (str(e.value), e.value.status) == (f"gemini API {status}: {said}", status)


@pytest.mark.parametrize("at", [0, 140, 150, 160, 300])          # the key runs past the cut from 130 to 162
def test_no_part_of_the_key_survives_what_a_service_said_cut_to_200_characters(choose, at):
    api = choose("compatible", ai_model="mixtral-9x")
    words = "x" * at + f"invalid header Authorization: Bearer {fo.KEY}; try again " + "y" * 300
    api.queue(fo.error(400, words))
    with pytest.raises(ModelError) as e:
        ask()
    said = str(e.value).removeprefix("compatible API 400: ")
    assert said == words.replace(fo.KEY, "<key>")[:200] and len(said) == 200
    assert not any(fo.KEY[:n] in said for n in range(6, len(fo.KEY) + 1))


@pytest.mark.parametrize("error, said", [
    ({"message": {"code": 401, "why": "café"}, "type": "invalid_request_error"}, '{"code":401,"why":"café"}'),
    (["bad", 401], '["bad",401]'),
    (True, "true"),
], ids=["a-message-that-is-an-object", "an-error-that-is-a-list", "an-error-that-is-true"])
def test_what_a_service_said_that_isnt_words_is_kept_as_compact_json(choose, error, said):
    from mailmind.bootstrap import assistant
    api = choose("compatible", ai_model="mixtral-9x")
    api.queue((400, {"error": error}))
    with pytest.raises(ModelError) as e:
        ask()
    assert (str(e.value), e.value.status) == (f"compatible API 400: {said}", 400)
    assert assistant.status.report()["last_error"] == "the API answered 400"


def test_a_list_asked_with_a_key_gemini_refuses_with_a_400_says_the_key_was_refused(openai_api):
    from mailmind.assistant.application.catalog import Catalog
    from mailmind.kernel.errors import Refused
    openai_api.key = None
    openai_api.queue(fo.gemini_error(400, "API key not valid. Please pass a valid API key.", "API_KEY_INVALID"))
    catalog = Catalog(lambda provider: "sk-test-wrong-key-abcdefghijkl", lambda provider: openai_api.url,
                      lambda wire, provider, base_url, key: list_models(provider, base_url, key), None)
    with pytest.raises(Refused, match="^The provider refused the key.$"):
        catalog.models("gemini")


def test_a_rate_limit_is_tried_again(choose):
    api = choose("openai")
    api.queue(fo.error(429, "Rate limit reached", NOW_RETRY, kind="rate_limit_exceeded"))
    assert ask() == {"body": "stub body"}
    assert len(api.requests) == 2


def test_a_persistent_outage_gives_up_after_two_retries(choose):
    from mailmind.bootstrap import assistant
    api = choose("openai")
    busy = fo.error(503, "The server is overloaded", NOW_RETRY, kind="server_error")
    api.queue(busy, busy, busy)
    with pytest.raises(ModelError, match="503") as e:
        ask()
    assert not e.value.permanent and len(api.requests) == 3
    assert assistant.status.report()["last_error"] == "the API answered 503"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_a_service_that_isnt_running_is_unreachable_and_status_says_it_couldnt_reach_it(choose, data_dir):
    from mailmind.bootstrap import assistant
    choose("local", ai_model="llama3.3", ai_base_url=f"http://127.0.0.1:{free_port()}/v1")
    with pytest.raises(ModelError, match="unreachable") as e:
        ask()
    assert e.value.status is None and not e.value.permanent
    assert assistant.status.report()["last_error"] == "couldn't reach it"


def test_a_model_on_this_mac_at_an_ipv6_address_is_asked_there():
    with fo.FakeOpenAI(key=None, host="::1") as api:
        assert api.url.startswith("http://[::1]:")
        model = ChatModel("local", api.url, "", "llama3.3", retries=0)
        assert model.complete(Prompt("Hi.", "llama3.3", 100)).text == fo.STUB_TEXT
        assert list_models("local", api.url, "") == list(fo.MODELS)
        assert [r.headers["host"] for r in api.requests] == [api.url.removeprefix("http://").removesuffix("/v1")] * 2


def test_the_adapter_alone_tries_a_refused_connection_again_only_as_told():
    model = ChatModel("local", f"http://127.0.0.1:{free_port()}/v1", "", "llama3.3", retries=0)
    with pytest.raises(ModelError, match="connection refused"):
        model.complete(Prompt("Hi.", "llama3.3", 100))


BROKEN_KEYS = [f"{fo.KEY}\n{fo.KEY}", f"{fo.KEY}\r\nX-Other: 1", f"sk-test {fo.KEY}", f"{fo.KEY}\t", f"{fo.KEY}\x00"]


@pytest.mark.parametrize("key", BROKEN_KEYS)
def test_a_key_with_a_blank_or_a_control_character_in_it_is_refused_unsent_and_never_tried_again(openai_api, key,
                                                                                               monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda s: pytest.fail("waited to try a key that can't be sent again"))
    model = ChatModel("openai", openai_api.url, key, "gpt-6.1-sol")
    with pytest.raises(ModelError) as e:
        model.complete(Prompt("Hi.", "gpt-6.1-sol", 100))
    assert (str(e.value), e.value.status, e.value.permanent) == ("openai API 401: the key was refused", 401, True)
    with pytest.raises(ModelError) as listed:
        list_models("openai", openai_api.url, key)
    assert (str(listed.value), listed.value.status) == ("openai API 401: the key was refused", 401)
    assert openai_api.requests == []


def test_a_key_that_breaks_its_header_is_never_in_the_error(openai_api, monkeypatch):
    import http.client

    def refusing(self, *args, **kwargs):
        raise ValueError(f"Invalid header value b'Bearer {fo.KEY}'")
    monkeypatch.setattr(http.client.HTTPConnection, "request", refusing)
    model = ChatModel("openai", openai_api.url, fo.KEY, "gpt-6.1-sol", retries=0)
    with pytest.raises(ModelError) as e:
        model.complete(Prompt("Hi.", "gpt-6.1-sol", 100))
    assert str(e.value) == "openai unreachable: ValueError" and fo.KEY[:6] not in str(e.value)


def test_a_batch_is_never_sent_to_another_provider():
    model = ChatModel("openai", "https://api.example.test/v1", fo.KEY, "gpt-6.1-sol")
    assert model.takes_batches is False
    for step in (lambda: model.submit_batch([("1", Prompt("Hi.", "m", 1))]), lambda: model.batch_ended("b"),
                 lambda: model.batch_results("b", "m"), lambda: model.discard_batch("b")):
        with pytest.raises(NoBatches, match="^Batches need Claude as the AI provider.$"):
            step()


# ── the models a provider lists ───────────────────────────────────────────────

def test_the_models_are_listed_with_the_key(openai_api):
    assert list_models("openai", openai_api.url, fo.KEY) == list(fo.MODELS)
    [req] = openai_api.requests
    assert (req.method, req.path, req.key) == ("GET", "/models", fo.KEY)


def test_a_list_asked_with_a_wrong_key_says_so_without_it(openai_api):
    with pytest.raises(ModelError) as e:
        list_models("openai", openai_api.url, "sk-test-wrong-key-abcdefghijkl")
    assert e.value.status == 401 and "ijkl" not in str(e.value)


# ── what the rest of the app sees ─────────────────────────────────────────────

def test_the_ledger_the_governor_and_the_status_see_these_calls_as_claudes(choose):
    from mailmind.bootstrap import assistant
    api = choose("openai")
    api.queue(fo.answer({"body": "x"}, model="gpt-6.1-sol", prompt_tokens=1000, completion_tokens=500,
                        prompt_tokens_details={"cached_tokens": 0}))
    ask()
    st = assistant.status.report()
    assert (st["engine"], st["provider"], st["off"], st["model"], st["key_from"], st["last_ok"]) == (
        "api", "openai", None, "gpt-6.1-sol", "mailmind.config.json", True)
    assert st["spent_today_micros"] == 1000 * 2 + 500 * 10          # gpt-6.1-sol's list price, in micro-dollars
    assert assistant.calls.usage(1)[0]["features"]["draft"] == {"calls": 1, "cost_micros": 7000}


def test_a_gemini_models_thinking_past_its_completion_is_priced_as_output(choose):
    from mailmind.bootstrap import assistant
    api = choose("gemini")
    api.queue(fo.answer({"body": "x"}, model="gemini-4-hydrogen", prompt_tokens=3000, completion_tokens=200,
                        total_tokens=4800))
    ask()
    # gemini-4-hydrogen's list price, $1.50 in and $7.50 out a million, on 3,000 in and 1,800 out
    assert assistant.status.report()["spent_today_micros"] == 4_500 + 13_500


def test_an_absurd_usage_is_recorded_at_the_most_the_ledger_holds(choose):
    from mailmind.bootstrap import assistant
    api = choose("compatible", key=None, ai_model="gpt-6.1-sol")
    api.key = None
    api.queue(fo.answer({"body": "x"}, model="gpt-6.1-sol", prompt_tokens=10**20, completion_tokens=5))
    ask()
    assert assistant.status.report()["spent_today_micros"] == 2**63 - 1
    assert assistant.calls.usage(1)[0]["features"]["draft"] == {"calls": 1, "cost_micros": 2**63 - 1}


def test_an_unknown_model_on_this_mac_costs_nothing(choose):
    from mailmind.bootstrap import assistant
    api = choose("local", key=None, ai_model="llama3.3")
    api.key = None
    api.queue(fo.answer({"body": "x"}, model="llama3.3", prompt_tokens=100_000, completion_tokens=100_000))
    ask()
    assert assistant.status.report()["spent_today_micros"] == 0


def test_a_model_on_this_mac_is_waited_for_longer_than_one_elsewhere(choose):
    from mailmind.bootstrap import assistant
    gateway = assistant.gateway()
    choose("local", key=None, ai_model="llama3.3")
    assert gateway._model()._http._timeout == 300
    choose("openai", ai_base_url="https://llm.example.test/v1")       # made, never asked
    assert gateway._model()._http._timeout == 120


def test_a_change_in_settings_takes_effect_at_once_and_the_model_is_made_once_for_each(choose):
    from mailmind.bootstrap import assistant
    gateway = assistant.gateway()
    choose("openai")
    first = gateway._model()
    assert isinstance(first, ChatModel) and gateway._model() is first
    choose("openai", ai_model="gpt-6-luna")
    assert gateway._model() is not first and gateway.model == "gpt-6-luna"
    choose("openai", ai_enabled=False)
    assert gateway.on is False
    with pytest.raises(AIOff, match="^AI is off: it is turned off in Settings$"):
        ask()
