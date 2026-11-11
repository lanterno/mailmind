"""The closed sets a request may name and the limits the page holds a person to: each declared once on the server, in the constant
its use case checks against, and reaching the page from there (the OpenAPI document, mailmind/http/page_limits.py), so the page
keeps no copy of its own.
"""

import pytest

import problems


# ── a value outside its set ──────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path, body", [
    ("/api/snooze", {"key": 1, "folder": "INBOX", "preset": "someday"}),
    ("/api/snooze", {"key": 1, "folder": "INBOX", "preset": ""}),
    ("/api/ai/draft-reply", {"key": 1, "tone": "sarcastic"}),
    ("/api/ai/rewrite", {"text": "hello", "op": "summarize"}),
    ("/api/bulk/file", {"verb": "move", "rows": [{"key": 1, "folder": "INBOX"}]}),
    ("/api/flag", {"key": 1, "folder": "INBOX", "flag": "deleted"}),
    ("/api/rules", {"token": "news@loom.example.test", "verdict": "maybe"}),
    ("/api/cleanup/execute", {"from_addr": "news@loom.example.test", "block_scope": "everyone"}),
    ("/api/invite/rsvp", {"key": 1, "decision": "Accept"}),
    ("/api/send", {"account": "ada@example.test", "mode": "draft", "to": "bea@example.test", "text": "hi"}),
    ("/api/draft", {"account": "ada@example.test", "mode": "draft", "text": "hi"}),
    ("/api/views", {"verb": "rename", "id": "x"}),
    ("/api/snippets", {"verb": "rename", "trigger": "x"}),
])
def test_a_value_outside_its_set_is_the_schemas_400(app, path, body):
    r = app.post(path, body)
    assert (r.status, r.json) == (400, problems.BAD_REQUEST)


@pytest.mark.parametrize("path, body, said", [
    ("/api/ai/rewrite", {"text": "hello"}, "unknown op"),
    ("/api/rules", {"token": "news@loom.example.test"}, "verdict must be keep|block|remove"),
    ("/api/views", {"name": "x", "q": "from:x"}, "verb must be create|delete"),
    ("/api/snippets", {"trigger": "x", "text": "y"}, "verb must be save|delete"),
])
def test_one_left_out_is_still_the_use_cases_to_word(app, path, body, said):
    r = app.post(path, body)
    assert (r.status, r.json) == (400, problems.refused(said))
