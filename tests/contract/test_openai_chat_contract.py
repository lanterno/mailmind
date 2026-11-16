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
        if key is not None and provider != "ollama":
            fake_keychain.add(f"{provider}-api-key", key, replace=True)
            top[f"{provider}_api_key"] = {"keychain": f"{provider}-api-key", "version": 1}
        prefs = {"ai_provider": provider, "ai_base_url": openai_api.url, **more}
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


@pytest.mark.parametrize("provider, model", [("gemini", "gemini-4-hydrogen"), ("compatible", "mixtral-9x"),
                                             ("ollama", "llama3.3")])
def test_the_others_ask_max_tokens_of_their_model(choose, provider, model):
    api = choose(provider, ai_model=model)
    if provider == "ollama":
        api.key = None
    ask()
    [req] = api.requests
    assert req.body["model"] == model and req.body["max_tokens"] == 16000 and "max_completion_tokens" not in req.body


def test_gemini_asks_its_own_model_unless_another_is_chosen(choose):
    api = choose("gemini")
    ask()
    assert api.requests[-1].body["model"] == "gemini-4-hydrogen"


def test_ollama_is_never_sent_a_key_even_one_the_file_names(choose, data_dir, fake_keychain):
    api = choose("ollama", ai_model="llama3.3")
    api.key = None
    written = json.loads((data_dir / "mailmind.config.json").read_text())
    fake_keychain.add("ollama-api-key", "sk-not-for-ollama", replace=True)
    written["ollama_api_key"] = {"keychain": "ollama-api-key", "version": 1}
    (data_dir / "mailmind.config.json").write_text(json.dumps(written))
    ask()
    assert "authorization" not in api.requests[0].headers


def test_a_compatible_service_is_sent_its_key_only_when_one_is_kept(choose):
    api = choose("compatible", key=None, ai_model="mixtral-9x")
    api.key = None
    ask()
    assert "authorization" not in api.requests[-1].headers
    choose("compatible", ai_model="mixtral-9x")
    ask()
    assert api.requests[-1].key == fo.KEY


def test_the_model_mailmind_model_names_wins_over_the_one_chosen(choose, monkeypatch):
    from mailmind.bootstrap import assistant
    api = choose("openai", ai_model="gpt-6-luna")
    monkeypatch.setattr(assistant.calls, "MODEL", "gpt-5-nano")
    ask()
    assert api.requests[-1].body["model"] == "gpt-5-nano" and assistant.calls.model_label() == "gpt-5-nano"


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

@pytest.mark.parametrize("provider", ["ollama", "compatible"])
def test_a_service_that_refuses_response_format_is_asked_once_more_without_it(choose, provider):
    api = choose(provider, key=None, ai_model="llama3.3")
    api.key, api.refuse_format = None, True
    api.queue(fo.error(400, "response_format of type json_schema is not supported"),
              fo.answer('Here it is: {"body": "See you Saturday."} Anything else?'))
    assert ask() == {"body": "See you Saturday."}
    first, second = api.chats
    assert "response_format" in first.body and "response_format" not in second.body
    assert second.body["messages"] == first.body["messages"]


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


# ── errors and retries ────────────────────────────────────────────────────────

def test_a_rejected_key_raises_without_a_retry_and_without_any_of_the_key(choose):
    from mailmind.bootstrap import assistant
    api = choose("openai", key="sk-test-wrong-key-abcdefghijkl")
    with pytest.raises(ModelError) as e:
        ask()
    assert e.value.status == 401 and e.value.permanent and len(api.requests) == 1
    assert "wrong" not in str(e.value) and "ijkl" not in str(e.value) and "sk-te" not in str(e.value)
    assert assistant.status.report()["last_error"] == "the API key was rejected"


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
    choose("ollama", ai_model="llama3.3", ai_base_url=f"http://127.0.0.1:{free_port()}/v1")
    with pytest.raises(ModelError, match="unreachable") as e:
        ask()
    assert e.value.status is None and not e.value.permanent
    assert assistant.status.report()["last_error"] == "couldn't reach it"


def test_the_adapter_alone_tries_a_refused_connection_again_only_as_told():
    model = ChatModel("ollama", f"http://127.0.0.1:{free_port()}/v1", "", "llama3.3", retries=0)
    with pytest.raises(ModelError, match="connection refused"):
        model.complete(Prompt("Hi.", "llama3.3", 100))


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


def test_an_unknown_model_on_this_mac_costs_nothing(choose):
    from mailmind.bootstrap import assistant
    api = choose("ollama", ai_model="llama3.3")
    api.key = None
    api.queue(fo.answer({"body": "x"}, model="llama3.3", prompt_tokens=100_000, completion_tokens=100_000))
    ask()
    assert assistant.status.report()["spent_today_micros"] == 0


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
