"""POST /api/filters: a filter made, changed and taken away through the route, a query Discovery can't judge on arrival refused
in its words, and what the route answers a change it can't make. What the use case decides is tests/unit/curation/test_filters.py."""

import pytest

import problems

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


def test_a_query_a_filter_cant_use_is_refused_in_discoverys_own_words(app):
    r = post(app, q="from:lin has:attachment")
    assert r.status == 400 and "attachments" in r.json["detail"]
    assert saved(app) == []


@pytest.mark.parametrize("verb", ["update", "delete"])
def test_naming_a_filter_there_isnt_is_a_404(app, verb):
    assert post(app, verb=verb, id="f-gone").status == 404


def test_a_verb_it_doesnt_know_is_a_bad_request(app):
    assert post(app, verb="move").json == problems.BAD_REQUEST
    r = app.post("/api/filters", {"q": "from:lin", "star": True})
    assert r.status == 400 and "verb must be" in r.json["detail"]
