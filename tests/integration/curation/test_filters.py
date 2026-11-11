"""A person's filters: what one may say, how they're kept in the rules file, and POST /api/filters."""

import pytest

import problems
from mailmind.curation.domain import filters

GOOD = {"verb": "create", "q": "from:news@loom.example.test", "star": True, "read": False}


def post(app, **body):
    return app.post("/api/filters", {**GOOD, **body})


def saved(app):
    return app.get("/api/rules").json["rules"]["filters"]


# ── the route ───────────────────────────────────────────────────────────────────────────────────

def test_create_list_update_and_delete(app):
    r = post(app)
    assert r.status == 200 and r.json["ok"]
    made = r.json["filters"][0]
    assert made["q"] == GOOD["q"] and made["star"] is True and made["read"] is False and made["id"]
    assert saved(app) == [made]
    r = post(app, verb="update", id=made["id"], q="subject:invoice", star=False, read=True)
    assert r.json["filters"] == [{"id": made["id"], "q": "subject:invoice", "star": False, "read": True}]
    r = post(app, verb="delete", id=made["id"], q=None, star=None, read=None)
    assert r.json["filters"] == []
    assert saved(app) == []


def test_a_query_is_kept_as_typed(app):
    made = post(app, q='  from:Lin "Thursday Lunch"  ').json["filters"][0]
    assert made["q"] == 'from:Lin "Thursday Lunch"'


def test_a_query_a_filter_cant_use_is_refused_in_its_own_words(app):
    r = post(app, q="from:lin has:attachment")
    assert r.status == 400 and "attachments" in r.json["detail"]
    assert saved(app) == []


def test_a_filter_must_do_something(app):
    r = post(app, star=False, read=False)
    assert r.status == 400 and "Choose what the filter does" in r.json["detail"]


@pytest.mark.parametrize("verb", ["update", "delete"])
def test_naming_a_filter_there_isnt_is_a_404(app, verb):
    assert post(app, verb=verb, id="f-gone").status == 404


def test_a_verb_it_doesnt_know_is_a_bad_request(app):
    assert post(app, verb="move").json == problems.BAD_REQUEST
    r = app.post("/api/filters", {"q": "from:lin", "star": True})
    assert r.status == 400 and "verb must be" in r.json["detail"]


def test_it_keeps_a_ceiling(app, monkeypatch):
    monkeypatch.setattr(filters, "MOST", 1)
    assert post(app).status == 200
    r = post(app, q="from:bea")
    assert r.status == 400 and "as many filters" in r.json["detail"]
    assert len(saved(app)) == 1
