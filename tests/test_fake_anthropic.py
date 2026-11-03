"""The fake Claude API answers the way the app's tests rely on: 401 without its key, a reply built
from the request's schema, queued replies first, and a record of every request. Driven through
claude_api.py, the app's own client."""
import pytest

from fakes import anthropic_api as fa

pytestmark = pytest.mark.usefixtures("data_dir")       # claude_api reads the config for a key first


def test_every_test_runs_against_the_fake(anthropic_api):
    import os
    assert os.environ["ANTHROPIC_BASE_URL"] == anthropic_api.url
    assert anthropic_api.url.startswith("http://127.0.0.1:")


def test_without_the_key_the_call_is_refused_and_still_recorded(anthropic_api):
    import claude_api
    with pytest.raises(RuntimeError, match="Claude API 401: invalid x-api-key"):
        claude_api.message("Hello from a test")
    assert [r.prompt for r in anthropic_api.requests] == ["Hello from a test"]
    assert anthropic_api.requests[0].headers["x-api-key"] == ""


def test_a_call_with_a_schema_gets_an_answer_built_from_it(ai):
    import json
    import claude_api
    schema = {"type": "object", "properties": {
        "summary": {"type": "string"}, "importance": {"type": "integer", "minimum": 1},
        "category": {"type": "string", "enum": ["finance", "other"]}, "urgent": {"type": "boolean"},
        "items": {"type": "array", "items": {"type": "object", "properties": {"who": {"type": "string"}}}},
        "maybe": {"type": ["null", "string"]}}}
    got = json.loads(claude_api.message("Classify this", schema))
    assert got == {"summary": "stub summary", "importance": 1, "category": "finance", "urgent": False,
                   "items": [{"who": "stub who"}], "maybe": "stub maybe"}
    req = ai.requests[0]
    assert req.headers["x-api-key"] == fa.KEY
    assert "minimum" not in json.dumps(req.schema)          # what claude_api sent, not the caller's
    assert req.schema["additionalProperties"] is False


def test_a_call_without_a_schema_gets_the_stub_text(ai):
    import claude_api
    assert claude_api.message("Say something") == fa.STUB_TEXT


def test_queued_replies_come_first_in_order(ai):
    import claude_api
    ai.queue(fa.answer("first"), lambda req: fa.answer(req.prompt.upper()),
             fa.error(400, "invalid_request_error", "bad request"))
    assert claude_api.message("one") == "first"
    assert claude_api.message("two") == "TWO"
    with pytest.raises(RuntimeError, match="Claude API 400: bad request"):
        claude_api.message("three")
    assert claude_api.message("four") == fa.STUB_TEXT
    assert [r.prompt for r in ai.requests] == ["one", "two", "three", "four"]


def test_requests_are_cleared_between_tests(anthropic_api):
    assert anthropic_api.requests == []


def test_instance_covers_the_schema_shapes():
    assert fa.instance({"anyOf": [{"type": "integer"}, {"type": "string"}]}) == 1
    assert fa.instance({"type": "array", "items": {"type": "string"}}, "tags") == ["stub tags"]
    assert fa.instance({"type": "object"}) == {}
    assert fa.instance("not a schema") is None
