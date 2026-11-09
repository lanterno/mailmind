"""What a call to the model sends and what is taken from its reply: the text after a fallback, a schema's `$defs`,
and a rewrite's prompt, which never asks for JSON."""


def test_hwf_only_the_text_after_the_last_fallback_block_counts(data_dir, ai):
    from mailmind.bootstrap import assistant
    hop = {"type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "claude-opus-4-8"}}
    ai.queue((200, {"type": "message", "role": "assistant", "stop_reason": "end_turn", "content": [
        {"type": "text", "text": '{"summary": "first try'}, hop,
        {"type": "text", "text": '{"summary": "second try'}, hop,
        {"type": "text", "text": '{"summary": "final", "importance": 3, "category": "info"}'}]}))
    assert assistant.calls.call_claude_json("Triage this.", assistant.assessments.TRIAGE_SCHEMA)["summary"] == "final"


def test_hwf_api_schema_reads_defs_as_names_not_keywords():
    from mailmind.assistant.domain.schema import structured
    schema = {"type": "object", "$defs": {"minimum": {"type": "integer", "minimum": 0}},
              "properties": {"n": {"$ref": "#/$defs/minimum"}}}
    assert structured(schema)["$defs"] == {"minimum": {"type": "integer"}}


def test_hwf_the_rewrite_prompt_never_asks_for_json(app, world, ai):
    assert app.post("/api/ai/rewrite", {"op": "shorten", "text": "thanks, see you thursday"}).status == 200
    [req] = ai.requests
    assert "json" not in req.prompt.lower()
    assert req.schema["properties"] == {"text": {"type": "string"}}     # the envelope is the schema's job
