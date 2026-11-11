"""The words a person keeps for the composer: what makes one, the file and the routes. What `;name` does in the text is the
page's, in features/compose."""
import json

import pytest

import problems

SIG = {"trigger": "sig", "text": "Thanks,\nAda"}


# ── the routes ────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_saved_it_is_listed_and_deleted_it_is_not(app, data_dir):
    assert app.get("/api/snippets").json == {"ok": True, "snippets": []}
    r = app.post("/api/snippets", {"verb": "save", "trigger": ";Sig", "text": SIG["text"]})
    assert (r.status, r.json) == (200, {"ok": True, "snippets": [SIG]})
    assert app.get("/api/snippets").json == {"ok": True, "snippets": [SIG]}
    assert json.loads((data_dir / "mailmind_snippets.json").read_text()) == [SIG]
    assert app.post("/api/snippets", {"verb": "delete", "trigger": "sig"}).json == {"ok": True, "snippets": []}
    assert app.get("/api/snippets").json["snippets"] == []


def test_deleting_what_isnt_there_is_no_refusal(app):
    assert app.post("/api/snippets", {"verb": "delete", "trigger": "gone"}).json == {"ok": True, "snippets": []}


def test_one_that_cant_be_kept_answers_why_and_keeps_what_was_there(app):
    app.post("/api/snippets", {"verb": "save", **SIG})
    r = app.post("/api/snippets", {"verb": "save", "trigger": "two words", "text": "x"})
    assert (r.status, r.json) == (400, problems.refused("a trigger is up to 32 letters, digits, - or _"))
    assert app.get("/api/snippets").json["snippets"] == [SIG]


@pytest.mark.parametrize("body", [{"verb": "save", "trigger": 5, "text": "x"}, {"verb": "save", "trigger": "s", "text": ["x"]}])
def test_a_field_that_wont_read_is_the_schemas_400(app, body):
    r = app.post("/api/snippets", body)
    assert (r.status, r.json) == (400, problems.BAD_REQUEST)
