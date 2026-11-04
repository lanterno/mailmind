"""The fake Claude API answers the way the app's tests rely on: 401 without its key, a reply built
from the request's schema, queued replies first, and a record of every request. Driven through the
Assistant's gateway, with the urllib client, the one the old front door runs (test_ai_contract.py
runs the SDK too)."""
import pytest

from fakes import anthropic_api as fa

pytestmark = pytest.mark.usefixtures("data_dir")       # the key is looked for in the config first


def ask(text, schema=None):
    from mailmind.bootstrap import assistant
    return assistant.gateway().ask(text, schema, feature="test").text


def test_every_test_runs_against_the_fake(anthropic_api):
    import os
    assert os.environ["ANTHROPIC_BASE_URL"] == anthropic_api.url
    assert anthropic_api.url.startswith("http://127.0.0.1:")


def test_a_wrong_key_is_refused_and_still_recorded(anthropic_api, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-the-fakes")
    with pytest.raises(RuntimeError, match="Claude API 401: invalid x-api-key"):
        ask("Hello from a test")
    assert [r.prompt for r in anthropic_api.requests] == ["Hello from a test"]
    assert anthropic_api.requests[0].key == "sk-ant-not-the-fakes"


def test_the_key_is_read_from_either_header():
    headers = {"x-api-key": fa.KEY}, {"authorization": f"Bearer {fa.KEY}"}, {"authorization": fa.KEY}
    assert [fa.Request(h, {}).key for h in headers] == [fa.KEY, fa.KEY, ""]


def test_a_call_with_a_schema_gets_an_answer_built_from_it(ai):
    import json
    schema = {"type": "object", "properties": {
        "summary": {"type": "string"}, "importance": {"type": "integer", "minimum": 1},
        "category": {"type": "string", "enum": ["finance", "other"]}, "urgent": {"type": "boolean"},
        "items": {"type": "array", "items": {"type": "object", "properties": {"who": {"type": "string"}}}},
        "maybe": {"type": ["null", "string"]}}}
    got = json.loads(ask("Classify this", schema))
    assert got == {"summary": "stub summary", "importance": 1, "category": "finance", "urgent": False,
                   "items": [{"who": "stub who"}], "maybe": "stub maybe"}
    req = ai.requests[0]
    assert req.key == fa.KEY
    assert "minimum" not in json.dumps(req.schema)          # what the app sent, not the caller's
    assert req.schema["additionalProperties"] is False


def test_a_call_without_a_schema_gets_the_stub_text(ai):
    assert ask("Say something") == fa.STUB_TEXT


def test_queued_replies_come_first_in_order(ai):
    ai.queue(fa.answer("first"), lambda req: fa.answer(req.prompt.upper()),
             fa.error(400, "invalid_request_error", "bad request"))
    assert ask("one") == "first"
    assert ask("two") == "TWO"
    with pytest.raises(RuntimeError, match="Claude API 400: bad request"):
        ask("three")
    assert ask("four") == fa.STUB_TEXT
    assert [r.prompt for r in ai.requests] == ["one", "two", "three", "four"]


def test_requests_are_cleared_between_tests(anthropic_api):
    assert anthropic_api.requests == []


def test_instance_covers_the_schema_shapes():
    assert fa.instance({"anyOf": [{"type": "integer"}, {"type": "string"}]}) == 1
    assert fa.instance({"type": "array", "items": {"type": "string"}}, "tags") == ["stub tags"]
    assert fa.instance({"type": "object"}) == {}
    assert fa.instance("not a schema") is None
