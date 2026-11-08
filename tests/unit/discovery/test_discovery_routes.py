"""Discovery's routes on their router: a query string of any text answers, what the schemas refuse and let by, and the one route's own catch."""
from urllib.parse import quote

import problems
from mailmind.bootstrap import assistant, discovery
from mailmind.bootstrap.mailbox import store
from mailmind.kernel.errors import Refused

BAD_REQUEST = problems.BAD_REQUEST
NOTHING_ASKED = {"ok": True, "query": "", "items": [], "total": 0, "has_more": False}
SECRET = "hunter2-secret-token"


# ── a search ─────────────────────────────────────────────────────────────────────────────────────

def test_a_search_for_any_text_answers(app):
    """Its grammar's parser can't be made to raise, and neither may the way there."""
    for q in ('"', '"not closed', "(", ")", "()", "AND", "OR", "NOT", "NEAR", "*", "a*", "*a", "-", "-x", ":", "::", "from:", "subject:", "is:", "is:nonsense",
              "has:", "before:", "after:never", "before:99999-99-99", "folder:\"", "category:", "lunch AND", "lunch OR (", "{}", "[]", "^", "'; DROP TABLE messages; --",
              "é", "日本語", "\U0001f600", "a\tb", "a\nb", "\x00", "a\x00b", "\x7f", "‮", "﻿", "%", "%%", "%00", "+", "&", ";", "=", "#", "?", "\\", "x" * 4000):
        r = app.get(f"/api/search?q={quote(q)}&scope=all")
        assert (r.status, r.json.get("ok")) == (200, True) or (r.status, r.json) == (400, problems.refused("bad query")), (q, r.body)


def test_a_query_string_of_any_shape_answers(app):
    for query in ("q=%", "q=%zz", "q=%ff%fe", "q=%00", "q=%ED%A0%80", "q=a;b", "q=a+b", "q=%2B", "q=a=b", "q==", "=", "&", "&&&", ";", "q", "%71=lunch", "q[]=lunch"):
        r = app.get(f"/api/search?{query}")
        assert (r.status, r.json.get("ok")) == (200, True) or (r.status, r.json) == (400, problems.refused("bad query")), (query, r.body)


def test_a_search_finds_what_it_finds(app):
    r = app.get("/api/search?q=lunch&scope=all")
    assert r.status == 200 and r.json["query"] == "lunch" and r.json["scope"] == "all" and r.json["items"]


def test_with_nothing_typed_it_answers_whatever_else_the_request_holds(app):
    for query in ("", "q=", "q=%20", "q=+++", "limit=x", "offset=y", "q=&limit=x&offset=y", "scope=all", "nobody=reads"):
        r = app.get(f"/api/search?{query}")
        assert (r.status, r.json) == (200, NOTHING_ASKED), query


def test_a_names_first_value_counts_and_a_blank_one_is_none(app):
    """As `parse_qs` read it. FastAPI would take the last, and hand a limit the blank."""
    r = app.get("/api/search?q=lunch&q=zzzzzz&scope=&account=&limit=&offset=").json
    assert (r["query"], r["scope"], r["offset"]) == ("lunch", "account", 0)


def test_a_number_may_stay_text_until_its_read(app):
    assert app.get("/api/search?q=lunch&scope=all&limit=1&offset=0").json["items"]
    assert app.get("/api/search?q=lunch&scope=all&limit=1&offset=x").json == BAD_REQUEST


def test_what_it_doesnt_read_is_nothing_to_refuse(app):
    r = app.get("/api/search?q=lunch&scope=all&nobody=reads&pk=x")
    assert (r.status, r.json["ok"]) == (200, True)


# ── documents, and who you know ──────────────────────────────────────────────────────────────────

def test_a_document_page_in_the_wrong_place_harms_no_one(app):
    for query in ("limit=x", "offset=y", "limit=x&offset=y", "limit=&offset=", "limit=99999999999999999999999", "q=%00", "kind=nonsense", "q=%22"):
        r = app.get(f"/api/documents?{query}")
        assert (r.status, r.json["ok"]) == (200, True), query


def test_a_document_says_who_sent_it(app, world):
    """`from` is no name for a field of a model, so its model calls it that by alias: the suite holds an answer to it."""
    c = store.conn()
    try:
        c.execute("INSERT INTO attachments(message_pk, part_id, filename, content_type, size, is_inline) VALUES (?,?,?,?,?,0)",
                  (world.pk("ada-inbox-03-newsletter.eml"), "2", "report.pdf", "application/pdf", 1000))
        c.commit()
    finally:
        c.close()
    [doc] = app.get("/api/documents").json["docs"]
    assert doc["filename"] == "report.pdf" and doc["from"] and "from_" not in doc


def test_autocomplete_takes_anything(app):
    for query in ("", "?q=", "?q=lin", "?q=%00", "?q=a&q=b"):
        r = app.get(f"/api/contacts{query}")
        assert (r.status, r.json["ok"]) == (200, True), query


def test_autocomplete_goes_by_the_first_value(app):
    assert app.get("/api/contacts?q=lin&q=zzzzzz").json == app.get("/api/contacts?q=lin").json
    assert app.get("/api/contacts?q=lin").json["items"]


def test_a_card_for_what_cant_be_an_address(app):
    for query in ("", "?addr=", "?addr=nobody", "?addr=%00"):
        r = app.get(f"/api/contact{query}")
        assert (r.status, r.json) == (400, problems.refused("bad address")), query


def test_a_card_for_who_you_know(app):
    r = app.get("/api/contact?addr=lin@example.test")
    assert (r.status, r.json["ok"], r.json["addr"]) == (200, True, "lin@example.test")


# ── saved views ──────────────────────────────────────────────────────────────────────────────────

def test_a_view_named_by_anything_but_text_is_the_400(app):
    before = app.get("/api/views").json
    for field in ("verb", "id", "name", "q"):
        for junk in (5, [1], {"a": 1}, True):
            r = app.post("/api/views", {"verb": "create", "name": "Lunch", "q": "lunch", field: junk})
            assert (r.status, r.json) == (400, BAD_REQUEST), (field, junk)
    assert app.get("/api/views").json == before


def test_a_change_it_cant_make_is_refused_in_its_own_words(app):
    for body, msg in (({}, "verb must be create|delete"), ({"verb": None}, "verb must be create|delete"), ({"verb": "rename"}, "verb must be create|delete"),
                      ({"verb": "create"}, "need a name and a query"), ({"verb": "create", "name": "Lunch"}, "need a name and a query"),
                      ({"verb": "create", "name": " ", "q": "lunch"}, "need a name and a query")):
        r = app.post("/api/views", body)
        assert (r.status, r.json) == (400, problems.refused(msg)), body


def test_a_field_nothing_here_reads_is_nothing_to_refuse(app):
    r = app.post("/api/views", {"verb": "create", "name": "Lunch", "q": "lunch", "subject": 5, "text": [1], "mode": 5})
    assert (r.status, r.json["ok"]) == (200, True)
    made = [v for v in r.json["views"] if v["name"] == "Lunch"]
    assert made and made[0]["builtin"] is False
    gone = app.post("/api/views", {"verb": "delete", "id": made[0]["id"]})
    assert gone.json["ok"] is True and not [v for v in gone.json["views"] if v["name"] == "Lunch"]


def test_a_path_with_both_methods_goes_by_the_method(app):
    assert app.get("/api/views").json["ok"] is True
    assert app.post("/api/views", {"verb": "delete", "id": "__unread__"}).json["ok"] is True


def test_a_method_a_route_doesnt_take_finds_no_route(app):
    for method, path in (("GET", "/api/contacts/known"), ("POST", "/api/search"), ("POST", "/api/contacts"), ("POST", "/api/contact"), ("POST", "/api/documents")):
        r = app.request(method, path, {} if method == "POST" else None)
        assert r.status == 404, (method, path)


# ── who is known ─────────────────────────────────────────────────────────────────────────────────

def test_going_without_a_list_keeps_its_own_words(app):
    for body in ({}, {"addrs": None}):
        r = app.post("/api/contacts/known", body)
        assert (r.status, r.json) == (400, problems.refused("addrs must be a list")), body


def test_addrs_that_isnt_a_list_is_the_400(app):
    for junk in ("lin@example.test", 5, {"lin@example.test": 1}, True):
        r = app.post("/api/contacts/known", {"addrs": junk})
        assert (r.status, r.json) == (400, BAD_REQUEST), junk


def test_what_a_list_holds_is_its_use_cases_to_judge(app):
    r = app.post("/api/contacts/known", {"addrs": ["lin@example.test", 5, None, ["x"], {"a": 1}, "not an address"]})
    assert r.status == 200 and r.json["items"]["lin@example.test"]["known"] is True
    assert r.json["bad"] == ["5", "", "['x']", "{'a': 1}", "not an address"]


def test_too_many_is_the_use_cases_too(app):
    r = app.post("/api/contacts/known", {"addrs": [f"p{i}@example.test" for i in range(101)]})
    assert (r.status, r.json) == (400, problems.refused("too many addresses"))


def test_what_asking_raises(app, monkeypatch):
    for error, status, answer in ((RuntimeError(SECRET), 500, problems.blank(500, "lookup failed")), (ValueError(SECRET), 500, problems.blank(500, "lookup failed")),
                                  (Refused("our own words"), 400, problems.refused("our own words"))):
        def boom(addrs, raised=error):
            raise raised
        monkeypatch.setattr(discovery.known, "ask", boom)
        r = app.post("/api/contacts/known", {"addrs": []})
        assert (r.status, r.json) == (status, answer), error


def test_a_request_counts_as_someone_here_once_it_reaches_its_route(app, monkeypatch):
    seen = []
    monkeypatch.setattr(assistant.governor(), "saw_someone", lambda: seen.append(1))
    assert app.post("/api/contacts/known", {"addrs": 5}).status == 400
    assert seen == []
    assert app.get("/api/search?q=lunch&scope=all").status == 200
    assert seen == [1]
