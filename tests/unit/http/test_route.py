"""mailmind.http.route.Route: what a route goes through, on a toy router behind the real app and perimeter, and what the app answers
where no route matches.
"""
import dataclasses
import http.client
import json
import threading
import time
from typing import Annotated

import pytest
from anyio import from_thread, to_thread
from fastapi import Query
from fastapi.openapi.utils import get_openapi
from starlette.responses import Response

import app_client
import problems
import schema_contract
from asgi_server import Uvicorn
from mailmind.assistant.api import AIOff, OverLimit
from mailmind.assistant.application import governor
from mailmind.http import shared
from mailmind.http.app import THREADS, create_app
from mailmind.http.route import Route, problem, router
from mailmind.http.schemas.common import Answer, Failure, Flag, NoBody, NotJson, Number, QueryArgs, RequestBody, answers
from mailmind.kernel.errors import Busy, Conflict, Locked, NotFound, Problem, Refused, Unreachable, Unreadable
from mailmind.platform import profile, settings
from mailmind.platform.db import txguard
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import correlation
from problems import CONTENT_TYPE

BAD_REQUEST = problems.BAD_REQUEST
TOO_LARGE = problems.blank(413, "payload too large")


class Body(RequestBody):
    text: str | None = None
    number: Number | None = None
    numbers: list[Number] | None = None
    flag: Flag | None = None


class Args(QueryArgs):
    limit: Number = 5
    q: str = ""


class Said(Answer):
    ok: bool
    got: dict


toy = router()


@toy.post("/api/toy", responses=answers(Said))
def post_toy(body: Body):
    return {"ok": True, "got": body.model_dump(exclude_unset=True)}


@toy.get("/api/toy", responses=answers(Said))
def get_toy(args: Annotated[Args, Query()]):
    return {"ok": True, "got": args.model_dump()}


@toy.get("/api/toy-query", responses=answers(Said))
def query_toy(args: Annotated[Args, Query()]):
    return {"ok": True, "got": args.as_query()}


@toy.post("/api/toy-empty", responses=answers(Said))
def empty_toy(body: NoBody):
    return {"ok": True, "got": {}}


@toy.get("/api/toy-only-read")
def only_read():
    return {"ok": True}


@toy.post("/api/toy-work", responses=answers(Said))
def post_work(body: Body):
    return {"ok": True, "got": {}}


@toy.get("/api/toy-work", responses=answers(Said))
def get_work():
    return {"ok": True, "got": {}}


def on(path, method):
    return next(route for route in toy.routes if route.path == path and method in route.methods)


@pytest.fixture
def served(data_dir):
    """The toy router behind the real perimeter, on uvicorn: a client as the page is one."""
    server = Uvicorn(create_app(routers=[toy], token=lambda: app_client.CSRF, body_cap=lambda method, path: 1 << 20), grace=0.2)
    server.start()
    try:
        with settings.use(dataclasses.replace(settings.current(), port=server.port)):
            yield app_client.AppClient(server.port)
    finally:
        server.stop()


def raw(client, method, path, body=None, headers=None, chunked=False):
    """The request as sent, and the answer as it came. `http.client` says no length only of a chunked body."""
    conn = http.client.HTTPConnection("127.0.0.1", client.port, timeout=30)
    try:
        sent = {"Host": f"127.0.0.1:{client.port}", "Sec-Fetch-Site": "same-origin", "X-CSRF-Token": app_client.CSRF, "Origin": client.origin,
                **(headers or {})}
        conn.request(method, path, body=body, headers=sent, encode_chunked=chunked)
        r = conn.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    finally:
        conn.close()


# ── what it answers ──────────────────────────────────────────────────────────────────────────────────

def test_an_answer_is_in_json_dumps_bytes(served, monkeypatch):
    monkeypatch.setattr(on("/api/toy-work", "GET"), "work", lambda: {"ok": True, "got": {"name": "é", "n": float("inf")}})
    monkeypatch.setattr(schema_contract, "HOLDING", False)
    status, headers, body = raw(served, "GET", "/api/toy-work")
    assert (status, body) == (200, b'{"ok": true, "got": {"name": "\\u00e9", "n": Infinity}}')
    assert headers["content-type"] == "application/json" and headers["content-length"] == str(len(body))


def test_the_perimeters_headers_go_on_every_answer(served):
    _, headers, _ = raw(served, "GET", "/api/toy-work")
    assert (headers["cache-control"], headers["x-frame-options"], headers["x-content-type-options"]) == ("no-store", "DENY", "nosniff")


def test_a_handler_may_say_its_status_or_write_its_own_answer(served, monkeypatch):
    route = on("/api/toy-work", "GET")
    monkeypatch.setattr(schema_contract, "HOLDING", False)
    monkeypatch.setattr(route, "work", lambda: ({"ok": True, "got": {}}, 202))
    assert served.get("/api/toy-work").status == 202
    monkeypatch.setattr(route, "work", lambda: problem(500, "its own words"))
    r = served.get("/api/toy-work")
    assert (r.status, r.headers["content-type"], r.json) == (500, CONTENT_TYPE, {"type": "about:blank", "title": "Internal Server Error", "status": 500, "detail": "its own words"})
    monkeypatch.setattr(route, "work", lambda: Response(b"hello", 201, headers={"Content-Type": "text/plain"}))
    status, headers, body = raw(served, "GET", "/api/toy-work")
    assert (status, headers["content-type"], body) == (201, "text/plain", b"hello")


def test_an_annotated_return_filters_nothing():
    """FastAPI would take the annotation for a response_model, and drop what it doesn't name."""
    r = router()

    @r.get("/api/annotated")
    def annotated() -> Said:
        return {"ok": True}     # type: ignore[return-value]
    assert r.routes[0].response_model is None


@pytest.mark.parametrize("wrong", ["async", "response_model", "no body"])
def test_what_a_route_may_not_be_is_refused_as_it_is_written(wrong):
    r = router()
    with pytest.raises(TypeError):
        if wrong == "async":
            @r.get("/api/wrong")
            async def on_the_loop():
                return {}
        elif wrong == "response_model":
            @r.get("/api/wrong", response_model=Said)
            def filtered():
                return {}
        else:
            @r.post("/api/wrong")
            def reads_nothing():
                return {}


# ── where no route matches ───────────────────────────────────────────────────────────────────────────

API_NOT_FOUND = json.dumps(problems.NO_ROUTE).encode()


def not_found(answer, api=True):
    """The 404 where no route matches: a problem document under /api/, as any failure there is, and a line of text elsewhere."""
    status, headers, body = answer
    said = (problems.CONTENT_TYPE, API_NOT_FOUND) if api else ("text/plain", b"not found")
    return (status, headers["content-type"], headers["content-length"], body) == (404, said[0], str(len(said[1])), said[1])


@pytest.mark.parametrize("method, path", [("GET", "/api/toy-empty"), ("POST", "/api/toy-only-read"), ("GET", "/api/no-such-route"),
                                          ("POST", "/api/no-such-route"), ("GET", "/no-such-page"), ("POST", "/no-such-page"), ("POST", "/")])
def test_a_get_or_a_post_no_route_takes_is_not_found(served, method, path):
    assert not_found(raw(served, method, path, b"{}" if method == "POST" else None), api=path.startswith("/api/"))


@pytest.mark.parametrize("body", [b"not json", b"[1]", b'{"account": 5}'])
def test_it_never_looks_at_a_post_it_has_no_route_for(served, body):
    assert not_found(raw(served, "POST", "/api/no-such-route", body))


def test_a_post_it_has_no_route_for_may_run_past_any_cap_undeclared(served):
    assert not_found(raw(served, "POST", "/api/no-such-route", iter([b"x" * (1 << 20), b"y"]), {"Transfer-Encoding": "chunked"}, chunked=True))


@pytest.mark.parametrize("path", ["/api/toy", "/api/no-such-route", "/no-such-page", "/", "*"])
@pytest.mark.parametrize("method", ["HEAD", "PUT", "DELETE", "PATCH", "OPTIONS", "TRACE"])
def test_any_other_method_isnt_implemented(served, method, path):
    status, headers, body = raw(served, method, path)
    said = ((problems.CONTENT_TYPE, json.dumps(problems.blank(501, "not implemented")).encode()) if path.startswith("/api/")
            else ("text/plain", b"not implemented"))
    assert (status, headers["content-type"], body) == (501, said[0], b"" if method == "HEAD" else said[1])
    assert headers["x-frame-options"] == "DENY"


def test_a_get_or_a_post_of_the_api_no_route_takes_says_someone_is_here(served, monkeypatch):
    seen = []
    monkeypatch.setattr(shared, "_someone_here", lambda: seen.append(1))
    raw(served, "GET", "/api/no-such-route")
    raw(served, "POST", "/api/no-such-route", b"{}")
    assert seen == [1, 1]
    raw(served, "GET", "/no-such-page")                                                   # no part of the API
    raw(served, "PUT", "/api/no-such-route")                                              # no method the app has
    raw(served, "POST", "/api/no-such-route", b"{}", {"X-CSRF-Token": "wrong"})           # no further than the perimeter
    assert seen == [1, 1]


@pytest.mark.parametrize("path", ["/api/toy-work", "/"])
@pytest.mark.parametrize("site", ["same-origin", "none"])
def test_an_absolute_form_request_line_names_no_route(served, monkeypatch, path, site):
    """The perimeter reads the whole of it as the path, which is no part of the API, so it must then match nothing either."""
    monkeypatch.setattr(on("/api/toy-work", "GET"), "work", lambda: pytest.fail("a route ran"))
    assert not_found(raw(served, "GET", f"http://127.0.0.1:{served.port}{path}", headers={"Sec-Fetch-Site": site}), api=False)


@pytest.mark.parametrize("path", ["/api/toy-work", "/api/no-such-route"])
def test_a_body_a_get_declares_is_not_waited_for(served, monkeypatch, path):
    """It may never come, and nothing on this path reads it."""
    conn = http.client.HTTPConnection("127.0.0.1", served.port, timeout=5)
    try:
        conn.putrequest("GET", path, skip_host=True)
        for name, value in (("Host", f"127.0.0.1:{served.port}"), ("Sec-Fetch-Site", "same-origin"), ("Content-Length", "5")):
            conn.putheader(name, value)
        conn.endheaders()
        assert conn.getresponse().status == (200 if path == "/api/toy-work" else 404)
    finally:
        conn.close()


@pytest.mark.parametrize("path", ["/%61pi/toy", "/api%2Ftoy", "/api/toy/", "//api/toy", "/API/toy", "/api/toy%00", "/api/toy;x", "/api//toy"])
def test_a_path_written_oddly_is_no_route_of_this_table(served, path):
    """uvicorn decodes a path, so `/%61pi/toy` would pass the perimeter as no part of the API and then match as /api/toy."""
    status, _, body = raw(served, "GET", path, headers={"Sec-Fetch-Site": ""})
    assert (status, body) in ((404, b"not found"), (404, API_NOT_FOUND),
                              (403, json.dumps(problems.blank(403, "same-origin request required")).encode()))


@pytest.mark.parametrize("path", ["/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"])
def test_the_schema_isnt_served(served, path):
    assert not_found(raw(served, "GET", path), api=False)


# ── a query string ───────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("query, got", [("", {"limit": 5, "q": ""}), ("?limit=7&q=a%20b", {"limit": 7, "q": "a b"}), ("?limit=", {"limit": 5, "q": ""}),
                                        ("?limit=1&limit=x", {"limit": 1, "q": ""}), ("?q=first&q=second", {"limit": 5, "q": "first"}),
                                        ("?other=1", {"limit": 5, "q": ""})])
def test_a_querys_first_value_counts_and_a_blank_is_none(served, query, got):
    assert served.get(f"/api/toy{query}").json == {"ok": True, "got": got}


@pytest.mark.parametrize("query", ["?limit=x", f"?limit={2 ** 53 + 1}", "?limit=99999999999999999999999", "?limit=1e400"])
def test_a_value_a_query_cant_mean_is_the_bad_request_400_on_a_read_too(served, query):
    r = served.get(f"/api/toy{query}")
    assert (r.status, r.json) == (400, BAD_REQUEST)


def test_a_use_case_that_takes_the_query_whole_gets_it_as_parse_qs_gives_it(served):
    assert served.get("/api/toy-query?limit=7&q=a").json["got"] == {"limit": ["7"], "q": ["a"]}


# ── a body ───────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("body", [b"[1]", b'"x"', b"5", b"null", b"true", b"not json", b"{", b"\xff\xfe\xff", b" "])
@pytest.mark.parametrize("path", ["/api/toy", "/api/toy-empty"])
def test_a_body_that_isnt_a_json_object_is_the_bad_request_400(served, path, body):
    status, headers, said = raw(served, "POST", path, body, {"Content-Type": "application/json"})
    assert (status, json.loads(said)) == (400, BAD_REQUEST) and headers["content-type"] == CONTENT_TYPE


@pytest.mark.parametrize("sent", [b"", b"{}"])
def test_no_body_is_an_empty_object(served, sent):
    assert raw(served, "POST", "/api/toy", sent)[2] == b'{"ok": true, "got": {}}'


@pytest.mark.parametrize("ctype", ["text/plain;charset=UTF-8", "application/x-www-form-urlencoded", None, "application/json; charset=utf-8"])
def test_a_body_is_json_whatever_its_content_type_says(served, ctype):
    status, _, said = raw(served, "POST", "/api/toy", b'{"number": 7}', {"Content-Type": ctype} if ctype else {})
    assert (status, json.loads(said)["got"]) == (200, {"number": 7})


def test_a_body_with_no_length_counts_for_nothing(served):
    """It reads as many bytes as Content-Length says."""
    status, _, said = raw(served, "POST", "/api/toy", iter([b'{"number": ', b"7}"]), {"Transfer-Encoding": "chunked"}, chunked=True)
    assert (status, said) == (200, b'{"ok": true, "got": {}}')


def test_a_body_that_runs_past_its_cap_undeclared_stops_there(served):
    """The perimeter judges only a declared length."""
    status, headers, said = raw(served, "POST", "/api/toy", iter([b"x" * (1 << 20), b"y"]), {"Transfer-Encoding": "chunked"}, chunked=True)
    assert (status, json.loads(said)) == (413, TOO_LARGE) and headers["content-type"] == CONTENT_TYPE


def test_a_declared_length_past_the_cap_is_the_perimeters(served):
    status, _, said = raw(served, "POST", "/api/toy", b"x" * ((1 << 20) + 1), {"Content-Type": "application/json"})
    assert (status, json.loads(said)) == (413, TOO_LARGE)


def test_a_declared_length_past_the_cap_isnt_waited_for(served):
    """It goes by the length alone, and the bytes may never come."""
    conn = http.client.HTTPConnection("127.0.0.1", served.port, timeout=5)
    try:
        conn.putrequest("POST", "/api/toy", skip_host=True)
        conn.putheader("Host", f"127.0.0.1:{served.port}")
        conn.putheader("Content-Length", str((1 << 20) + 1))
        conn.endheaders()
        conn.send(b"{}")
        r = conn.getresponse()
        assert (r.status, json.loads(r.read())) == (413, TOO_LARGE)
    finally:
        conn.close()


def test_it_comes_after_the_perimeter(served):
    assert raw(served, "POST", "/api/toy", b"not json", {"X-CSRF-Token": "wrong"})[0] == 403
    assert raw(served, "POST", "/api/toy", b"not json", {"Origin": "http://evil.example.test"})[0] == 403


# ── what a schema lets through, and refuses ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("given, got", [({"number": 7}, {"number": 7}), ({"number": "7"}, {"number": 7}), ({"number": 7.5}, {"number": 7}),
                                        ({"number": True}, {"number": 1}), ({"number": 2 ** 53}, {"number": 2 ** 53}), ({"number": None}, {"number": None}),
                                        ({"text": "hi"}, {"text": "hi"}), ({"text": None}, {"text": None}), ({"flag": False}, {"flag": False}),
                                        ({"numbers": [1, "2"]}, {"numbers": [1, 2]}), ({"unknown": [1]}, {}), ({}, {})])
def test_what_a_model_lets_through(served, given, got):
    r = served.post("/api/toy", given)
    assert (r.status, r.json["got"]) == (200, got)


@pytest.mark.parametrize("given", [{"text": 5}, {"text": True}, {"text": 1.5}, {"text": ["x"]}, {"text": {"a": 1}},
                                   {"number": "x"}, {"number": 2 ** 53 + 1}, {"number": 10 ** 30}, {"number": [1]}, {"number": {}}, {"number": ""},
                                   {"numbers": [1, "x"]}, {"numbers": [None]}, {"numbers": "x"}, {"numbers": 5},
                                   {"flag": "no"}, {"flag": 0}, {"flag": []}])
def test_what_a_model_refuses_is_the_bad_request_400(served, given):
    r = served.post("/api/toy", given)
    assert (r.status, r.json) == (400, BAD_REQUEST)


@pytest.mark.parametrize("junk", [b"Infinity", b"-Infinity", b"NaN"])
def test_a_number_json_can_say_and_a_row_cant_hold(served, junk):
    status, _, said = raw(served, "POST", "/api/toy", b'{"number": ' + junk + b"}")
    assert (status, json.loads(said)) == (400, BAD_REQUEST)


# ── what a failure answers ───────────────────────────────────────────────────────────────────────────

def raising(monkeypatch, raised):
    def boom(*args, **kwargs):
        raise raised
    for method in ("GET", "POST"):
        monkeypatch.setattr(on("/api/toy-work", method), "work", boom)


def asked(served, how):
    return served.get("/api/toy-work") if how == "GET" else served.post("/api/toy-work", {})


@pytest.mark.parametrize("how", ["GET", "POST"])
@pytest.mark.parametrize("raised, doc", [(NotFound("gone"), problems.not_found("gone")), (Conflict("moved", conflict=True, version=7), problems.conflict("moved", conflict=True, version=7)),
                                         (Locked("held"), problems.locked("held")), (Refused(), problems.refused()), (Unreachable("away"), problems.unreachable("away")),
                                         (Unreadable("garbled"), problems.unreadable("garbled")), (Busy(), problems.busy()), (NotFound(), problems.not_found()),
                                         (OverLimit(7, 5), problems.over_limit(7, 5)), (AIOff("hunter2"), problems.ai_off())],
                         ids=lambda v: type(v).__name__ if isinstance(v, Exception) else "")
def test_a_problem_answers_what_it_means(served, monkeypatch, how, raised, doc):
    raising(monkeypatch, raised)
    monkeypatch.setattr(schema_contract, "HOLDING", False)
    r = asked(served, how)
    assert (r.status, r.headers["content-type"], r.json) == (doc["status"], CONTENT_TYPE, doc)


@pytest.mark.parametrize("raised", [TypeError("hunter2"), ValueError("hunter2")], ids=repr)
def test_a_type_or_value_error_is_a_malformed_post_and_on_a_read_a_failure(served, monkeypatch, raised):
    raising(monkeypatch, raised)
    r = served.post("/api/toy-work", {})
    assert (r.status, r.json) == (400, BAD_REQUEST)
    r = served.get("/api/toy-work")
    assert (r.status, r.json) == (500, problems.FAILED)


def test_a_write_this_process_may_not_make_is_a_409_on_a_post_and_on_a_read_a_failure(served, monkeypatch):
    raising(monkeypatch, profile.ReadOnlyError("hunter2"))
    r = served.post("/api/toy-work", {})
    assert (r.status, r.json) == (409, problems.read_only())
    assert served.get("/api/toy-work").status == 500


@pytest.mark.parametrize("how", ["GET", "POST"])
@pytest.mark.parametrize("raised", [RuntimeError("hunter2"), KeyError("hunter2"), Problem("hunter2"), OSError(13, "hunter2", "hunter2")], ids=repr)
def test_anything_else_is_the_fixed_500_with_its_class_in_the_log_and_no_traceback(served, monkeypatch, capsys, raised, how):
    raising(monkeypatch, raised)
    r = asked(served, how)
    assert (r.status, r.json) == (500, problems.FAILED)
    out = capsys.readouterr()
    assert f"[http] {how} /api/toy-work failed: {type(raised).__name__}\n" in out.out
    assert "hunter2" not in out.out + out.err and "Traceback" not in out.err


# ── what it runs on ──────────────────────────────────────────────────────────────────────────────────

def test_a_handler_runs_on_the_pool_with_its_requests_context(served, monkeypatch):
    seen = []

    def noting(*args, **kwargs):
        seen.append((threading.current_thread().name, correlation().get("request_id"), governor._over.get()))
        return {"ok": True, "got": {}}
    monkeypatch.setattr(on("/api/toy-work", "POST"), "work", noting)
    served.post("/api/toy-work", {})
    served.post("/api/toy-work", {}, headers={"X-MailMind-AI-Over-Limit": "allow"})
    served.post("/api/toy-work", {}, headers={"X-MailMind-AI-Over-Limit": "yes"})
    served.post("/api/toy-work", {})
    assert {name for name, _, _ in seen} == {"AnyIO worker thread"}
    assert len({request_id for _, request_id, _ in seen} - {None}) == 4
    assert [over for _, _, over in seen] == [False, True, False, False]


def test_a_thread_used_again_inherits_nothing_of_the_last_request(served, monkeypatch):
    """A request that left a transaction open, with something still holding its connection, as an exception's traceback can. On a
    thread of its own that would die with it. On one from the pool the next request would be refused every network call, and
    every writer would wait a minute for the lock."""
    kept, seen = [], []

    def leaves_a_transaction(*args, **kwargs):
        c = UnitOfWork().connect()
        c.execute("BEGIN IMMEDIATE")
        kept.append(c)
        seen.append(threading.get_ident())
        return {"ok": True, "got": {}}

    def next_request(*args, **kwargs):
        seen.append(threading.get_ident())
        txguard.refuse_network("a network call")
        with UnitOfWork().write() as c:
            c.execute("CREATE TABLE IF NOT EXISTS toy (x)")
        return {"ok": True, "got": {"open": txguard.open_transactions()}}
    monkeypatch.setattr(on("/api/toy-work", "POST"), "work", leaves_a_transaction)
    assert served.post("/api/toy-work", {}).status == 200
    monkeypatch.setattr(on("/api/toy-work", "POST"), "work", next_request)
    r = served.post("/api/toy-work", {})
    assert (r.status, r.json["got"]) == (200, {"open": 0})
    assert seen[0] == seen[1], "the pool gave the second request another thread, so this tested nothing"
    with pytest.raises(Exception):
        kept[0].execute("SELECT 1")     # closed


def test_what_a_failed_request_left_is_gone_too(served, monkeypatch):
    seen = []

    def leaves_and_raises(*args, **kwargs):
        c = UnitOfWork().connect()
        c.execute("BEGIN IMMEDIATE")
        seen.append(c)
        raise RuntimeError("hunter2")
    monkeypatch.setattr(on("/api/toy-work", "POST"), "work", leaves_and_raises)
    assert served.post("/api/toy-work", {}).status == 500
    monkeypatch.setattr(on("/api/toy-work", "POST"), "work", lambda *a, **k: {"ok": True, "got": {"open": txguard.open_transactions()}})
    assert served.post("/api/toy-work", {}).json["got"] == {"open": 0}


def test_a_request_past_the_guards_says_someone_is_here_unless_it_only_polls(served, monkeypatch):
    seen = []
    monkeypatch.setattr(shared, "_someone_here", lambda: seen.append(1))
    served.get("/api/toy-work")
    assert seen == [1]
    served.post("/api/toy-work", {"text": 5})                                 # no further than its schema
    served.post("/api/toy-work", {}, headers={"X-CSRF-Token": "wrong"})       # no further than the perimeter
    assert seen == [1]
    monkeypatch.setattr(shared, "UI_QUIET", {"/api/toy-work"})
    served.get("/api/toy-work")
    assert seen == [1]


def test_startup_raises_the_pools_limit(served, monkeypatch):
    def limit():
        return from_thread.run_sync(lambda: to_thread.current_default_thread_limiter().total_tokens)
    monkeypatch.setattr(on("/api/toy-work", "GET"), "work", lambda: {"ok": True, "got": {"limit": limit()}})
    assert served.get("/api/toy-work").json["got"] == {"limit": THREADS} and THREADS == 64


def test_more_requests_wait_at_once_than_anyio_starts_with(served, monkeypatch):
    """A click can wait a long time on an IMAP gate or the Claude API, and 40 of those shouldn't stop the 41st."""
    arrived, proceed = [], threading.Event()

    def waiting(*args, **kwargs):
        arrived.append(1)
        proceed.wait(15)
        return {"ok": True, "got": {}}
    monkeypatch.setattr(on("/api/toy-work", "GET"), "work", waiting)
    clients = [threading.Thread(target=served.get, args=("/api/toy-work",)) for _ in range(45)]
    try:
        for t in clients:
            t.start()
        deadline = time.monotonic() + 10
        while len(arrived) < 45 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(arrived) == 45
    finally:
        proceed.set()
        for t in clients:
            t.join(15)


def test_a_blocked_handler_doesnt_hold_the_server_past_its_grace(data_dir, monkeypatch):
    """A blocked one doesn't hold the exit: the pool's threads are daemons."""
    release = threading.Event()
    monkeypatch.setattr(on("/api/toy-work", "GET"), "work", lambda: release.wait(30) and {"ok": True, "got": {}})
    server = Uvicorn(create_app(routers=[toy], token=lambda: app_client.CSRF, body_cap=lambda method, path: 0), grace=0.2)
    server.start()
    try:
        with settings.use(dataclasses.replace(settings.current(), port=server.port)):
            client = app_client.AppClient(server.port)
            threading.Thread(target=lambda: _quietly(client), daemon=True).start()
            time.sleep(0.3)
            began = time.monotonic()
            server.stop()
            assert time.monotonic() - began < 2
    finally:
        release.set()


def _quietly(client):
    try:
        client.get("/api/toy-work")
    except OSError:
        pass


# ── the schema a script can have ─────────────────────────────────────────────────────────────────────

def test_openapi_reads_what_a_route_takes_and_answers(data_dir):
    app = create_app(routers=[toy], token=lambda: app_client.CSRF, body_cap=lambda method, path: 0)
    assert app.openapi_url is None
    schema = get_openapi(title="MailMind", version="0", routes=app.routes)
    post = schema["paths"]["/api/toy"]["post"]
    assert post["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/Body")
    assert post["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/Said")
    assert post["responses"]["409"]["content"]["application/json"]["schema"]["$ref"].endswith("/Failure")
    assert schema["components"]["schemas"]["Body"]["properties"]["number"]["anyOf"][0]["type"] == "integer"
    assert {p["name"] for p in schema["paths"]["/api/toy"]["get"]["parameters"]} == {"limit", "q"}


def test_a_union_of_answers_reads_as_one(data_dir):
    class Refused_(Answer):
        ok: bool
        msg: str
    r = router()

    @r.post("/api/either", responses=answers(Said | Refused_))
    def either(body: NoBody):
        return {"ok": True, "got": {}}
    schema = get_openapi(title="MailMind", version="0", routes=create_app(routers=[r], token=lambda: "", body_cap=lambda m, p: 0).routes)
    assert len(schema["paths"]["/api/either"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["anyOf"]) == 2


def test_an_answer_that_isnt_json_says_so(data_dir):
    """Its 200 holds no model and no media type, so the page's types give it no JSON. What it answers when it can't is still JSON."""
    r = router()

    @r.get("/api/file", responses=answers(NotJson))
    def file():
        return Response(b"bytes", headers={"Content-Type": "application/octet-stream"})
    route = r.routes[0]
    assert "model" not in route.responses[200] and route.response_class is Response
    schema = get_openapi(title="MailMind", version="0", routes=create_app(routers=[r], token=lambda: "", body_cap=lambda m, p: 0).routes)
    said = schema["paths"]["/api/file"]["get"]["responses"]
    assert "content" not in said["200"] and "NotJson" not in schema["components"]["schemas"]
    assert said["404"]["content"]["application/json"]["schema"]["$ref"].endswith("/Failure")     # tests/support/openapi_doc.py says what it is


def test_a_failure_says_nothing_it_doesnt_name():
    Failure.model_validate({"type": "/problems/busy", "title": "Busy", "status": 409, "busy": True})
    Failure.model_validate({"type": "about:blank", "title": "Gone", "status": 410, "detail": "x"})
    with pytest.raises(ValueError):
        Failure.model_validate({"type": "about:blank", "title": "Gone", "status": 410, "surprise": 1})
    with pytest.raises(ValueError):
        Failure.model_validate({"ok": False, "error": "x"})


def test_every_route_is_a_route():
    assert all(isinstance(route, Route) for route in toy.routes)
