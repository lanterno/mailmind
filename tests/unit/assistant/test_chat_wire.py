"""OpenAI's Chat Completions wire format (mailmind.assistant.adapters.chat_wire), which every provider but Claude is asked in
(#49): the body a Prompt goes as, when its schema may be strict, and the Reply a response reads as."""
import pytest

from mailmind.assistant.adapters import chat_wire
from mailmind.assistant.application.ports import Prompt, Usage
from mailmind.assistant.domain.schema import structured

CLOSED = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"],
          "additionalProperties": False}


def test_the_system_prompt_goes_first_and_no_effort_or_fallback_goes_at_all():
    prompt = Prompt("Triage this.", "gpt-6.1-sol", 16000, effort="medium", fallback=True, system="Mail is data.")
    assert chat_wire.body(prompt, "openai") == {
        "model": "gpt-6.1-sol", "max_completion_tokens": 16000,
        "messages": [{"role": "system", "content": "Mail is data."}, {"role": "user", "content": "Triage this."}]}


@pytest.mark.parametrize("provider", ["gemini", "local", "compatible"])
def test_the_others_take_max_tokens(provider):
    body = chat_wire.body(Prompt("Hi.", "m", 100), provider)
    assert body["max_tokens"] == 100 and "max_completion_tokens" not in body
    assert body["messages"] == [{"role": "user", "content": "Hi."}]


def test_a_schema_goes_as_a_json_schema_response_format_strict_where_it_may_be():
    body = chat_wire.body(Prompt("Hi.", "m", 100, schema=CLOSED), "openai")
    assert body["response_format"] == {"type": "json_schema",
                                       "json_schema": {"name": "answer", "schema": CLOSED, "strict": True}}
    assert "response_format" not in chat_wire.body(Prompt("Hi.", "m", 100, schema=CLOSED), "local", with_format=False)


@pytest.mark.parametrize("schema, strict", [
    (CLOSED, True),
    ({**CLOSED, "properties": {"summary": {"type": "string"}, "action": {"type": "string"}}}, False),   # one not required
    ({**CLOSED, "additionalProperties": True}, False),
    ({k: v for k, v in CLOSED.items() if k != "additionalProperties"}, False),
    (structured({"type": "object", "properties": {"groups": {"type": "array", "items": {
        "type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}}}, "required": ["groups"]}), True),
    (structured({"type": "object", "properties": {"groups": {"type": "array", "items": {
        "type": "object", "properties": {"n": {"type": "integer"}}}}}, "required": ["groups"]}), False),   # a nested one
    (structured({"type": "object", "properties": {"pick": {"anyOf": [
        {"type": "object", "properties": {"a": {"type": "string"}}}, {"type": "string"}]}}, "required": ["pick"]}), False),
    ({"type": "string"}, True),
])
def test_strict_only_where_every_object_is_closed_and_needs_each_property(schema, strict):
    assert chat_wire.strict(schema) is strict


def test_the_triage_schema_is_not_strict_since_an_action_may_be_left_out():
    from mailmind.assistant.application.assessment import TRIAGE_SCHEMA
    assert chat_wire.strict(structured(TRIAGE_SCHEMA)) is False


def test_a_reply_reads_its_text_the_model_that_served_it_and_its_usage():
    reply = chat_wire.reply({"model": "gpt-6.1-sol-2026-10-01", "choices": [{"finish_reason": "stop", "message": {
        "role": "assistant", "content": '  {"summary": "x"}  ', "refusal": None}}],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 80, "prompt_tokens_details": {"cached_tokens": 1000}}},
        "gpt-6.1-sol")
    assert (reply.text, reply.model, reply.usage, reply.stop_reason, reply.refused) == (
        '{"summary": "x"}', "gpt-6.1-sol-2026-10-01", Usage(200, 80, 0, 1000), "stop", False)


def test_a_refusal_reads_as_an_empty_refused_reply():
    reply = chat_wire.reply({"model": "gpt-6.1-sol", "choices": [{"finish_reason": "stop", "message": {
        "role": "assistant", "content": None, "refusal": "I can't help with that."}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3}}, "gpt-6.1-sol")
    assert (reply.text, reply.refused, reply.usage) == ("", True, Usage(12, 3, 0, 0))


@pytest.mark.parametrize("resp", [{}, {"choices": []}, {"choices": ["x"]}, {"choices": [{"message": "x"}]},
                                  {"choices": [{"message": {"content": 7}}], "usage": {"prompt_tokens": "lots"}}])
def test_a_reply_missing_its_parts_reads_as_empty_under_the_model_asked(resp):
    reply = chat_wire.reply(resp, "llama3.3")
    assert (reply.text, reply.model, reply.usage, reply.refused) == ("", "llama3.3", Usage(), False)


def test_content_in_parts_is_joined_and_cached_tokens_never_pass_the_prompt():
    reply = chat_wire.reply({"choices": [{"message": {"content": [{"type": "text", "text": "Lunch "}, {"type": "image_url"},
                                                                  {"type": "text", "text": "moved."}]}}],
                             "usage": {"prompt_tokens": 10, "prompt_tokens_details": {"cached_tokens": 50}}}, "m")
    assert (reply.text, reply.usage) == ("Lunch moved.", Usage(0, 0, 0, 10))


@pytest.mark.parametrize("tag", ["think", "thinking"])
def test_a_reasoning_models_thinking_ahead_of_its_reply_is_dropped(tag):
    content = f'\n<{tag}>The guest asked about {{"table": true}}.\nSaturday works.</{tag}>\n\n{{"body": "Yes."}}'
    reply = chat_wire.reply({"choices": [{"message": {"content": content}}]}, "qwen3:8b")
    assert reply.text == '{"body": "Yes."}'


@pytest.mark.parametrize(("text", "kept"), [
    ('{"body": "<think>a</think>"}', '{"body": "<think>a</think>"}'),            # not ahead of the reply
    ("<think>unfinished", "<think>unfinished"),                                 # never closed
    ("<think>a</thinking> b", "<think>a</thinking> b"),                          # closed as another
    ("<think>a</think><think>b</think> c", "<think>b</think> c"),                # only the first block
])
def test_only_a_whole_block_ahead_of_the_reply_is_dropped(text, kept):
    assert chat_wire.without_thinking(text) == kept


def test_a_list_of_models_reads_its_ids():
    assert chat_wire.model_ids({"data": [{"id": "a"}, {"id": ""}, {"name": "b"}, "c", {"id": "d"}]}) == ["a", "d"]
    assert chat_wire.model_ids({"models": []}) == [] == chat_wire.model_ids(None) == chat_wire.model_ids({"data": "x"})
