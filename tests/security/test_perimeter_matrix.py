"""The perimeter matrix: every route the app has, and what no route matches, against each way a request can be wrong. The walk
reads the routers, so a route added tomorrow is in it. test_perimeter.py pins each check on its own, and test_http_post_guards.py
a few routes end to end.

Every route is swapped for one that only says it ran. So the walk can knock on all of them, the event stream and a send
included, with nothing happening, and an honest request reaching each shows the refusals are the perimeter's own.
"""
import json

import app_client
import pytest
from fastapi.routing import iter_route_contexts

import route_tables
import schema_contract
from mailmind.bootstrap import front
from mailmind.http import app as http_app
from mailmind.http.route import Route
from mailmind.http.shared import send_whole

BAD_HOST = "bad host/origin"
CROSS_SITE = "cross-site request"
NOT_THE_PAGE = "same-origin request required"
BAD_TOKEN = "bad or missing CSRF token"


def image(site):
    return {"Sec-Fetch-Site": site, "Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "image"}


def wrong_for_any_request(port):
    """What is wrong whatever the method: (what it is, its headers, the refusal)."""
    return [("a rebound name", {"Host": "rebound.example.test"}, BAD_HOST),
            ("a rebound name on this port", {"Host": f"rebound.example.test:{port}"}, BAD_HOST),
            ("this machine on another port", {"Host": f"127.0.0.1:{port + 1}"}, BAD_HOST),
            ("this name with no port", {"Host": "127.0.0.1"}, BAD_HOST),
            ("another site's Origin", {"Origin": "http://evil.example.test"}, BAD_HOST),
            ("this name by another scheme", {"Origin": f"https://127.0.0.1:{port}"}, BAD_HOST),
            ("no origin to speak of", {"Origin": "null"}, BAD_HOST),
            ("another site's image", image("cross-site"), CROSS_SITE),
            ("another port's image", image("same-site"), CROSS_SITE)]


def wrong_for_a_post():
    return [("a request that says nothing of where it came from", {"Sec-Fetch-Site": None}, NOT_THE_PAGE),
            ("a typed URL", {"Sec-Fetch-Site": "none"}, NOT_THE_PAGE),
            ("no Origin", {"Origin": None}, "origin required"),
            ("no token", {"X-CSRF-Token": None}, BAD_TOKEN),
            ("an empty token", {"X-CSRF-Token": ""}, BAD_TOKEN),
            ("a wrong token", {"X-CSRF-Token": "not-the-token"}, BAD_TOKEN)]


def wrong_for_an_api_read():
    return [("a request that says nothing of where it came from", {"Sec-Fetch-Site": None}, NOT_THE_PAGE),
            ("a typed URL", {"Sec-Fetch-Site": "none"}, NOT_THE_PAGE)]


class Table:
    """Every route answering only that it ran. A path no route takes stays unknown."""

    def __init__(self):
        self.reached = []
        self.walk = sorted(route_tables.paths())

    async def ran(self, route, scope, send):
        path = route_tables.concrete(route.path)
        self.reached.append((scope["method"], path))
        await send_whole(send, 200, [(b"content-type", b"application/json")], json.dumps({"ok": True, "ran": path}).encode())

    def routes(self, method=None):
        """Every route, as (method, a path it answers)."""
        return [r for r in self.walk if method in (None, r[0])]


@pytest.fixture
def table(app, monkeypatch):
    spy = Table()
    route_tables.stand_in(monkeypatch, spy.ran)
    monkeypatch.setattr(app_client, "RECORD_API", None)     # what these answer isn't what the routes answer
    monkeypatch.setattr(schema_contract, "HOLDING", False)
    return spy


def knock(app, method, path, headers):
    return app.request(method, path, {} if method == "POST" else None, headers=headers)


def refusals(app, table, routes, cases):
    """What didn't answer as it should: each route, in each way it can be wrong."""
    wrong = []
    for method, path in routes:
        for what, headers, error in cases:
            table.reached.clear()
            r = knock(app, method, path, headers)
            said = r.body if isinstance(r.body, dict) else r.body[:80]
            if r.status != 403 or said != {"ok": False, "error": error} or table.reached:
                wrong.append((method, path, what, r.status, said))
    return wrong


def test_the_walk_holds_every_route():
    walk = Table().routes()
    assert len(walk) == sum(len(route.methods) for route in route_tables.every_route())
    assert {("GET", "/"), ("GET", "/api/events"), ("POST", "/api/send"), ("POST", "/api/cleanup/execute"),
            ("POST", "/api/ai/do-confirm")} <= set(walk)


def test_an_honest_request_reaches_every_route(app, table):
    """So that a refusal below is the perimeter refusing, and not this walk missing the door."""
    for method, path in table.routes():
        table.reached.clear()
        r = knock(app, method, path, {})
        assert (r.status, table.reached) == (200, [(method, path)]), (method, path, r.status)


def test_every_read_is_refused_from_anywhere_else(app, table):
    assert refusals(app, table, table.routes("GET"), wrong_for_any_request(app.port)) == []


def test_every_read_of_the_api_is_refused_unless_the_page_asked(app, table):
    api = [r for r in table.routes("GET") if r[1].startswith("/api/")]
    assert refusals(app, table, api, wrong_for_an_api_read()) == []


def test_every_post_is_refused_from_anywhere_else(app, table):
    assert refusals(app, table, table.routes("POST"), wrong_for_any_request(app.port)) == []


def test_every_post_is_refused_without_its_origin_and_its_token(app, table):
    assert refusals(app, table, table.routes("POST"), wrong_for_a_post()) == []


def test_another_sites_link_opens_the_page_and_nothing_else(app, table):
    """A top-level visit to `/` is the one thing another site may start."""
    def from_a_site(dest):
        return {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": dest}
    assert (knock(app, "GET", "/", from_a_site("document")).status, table.reached) == (200, [("GET", "/")])
    others = [r for r in table.routes("GET") if r[1] != "/"]
    assert refusals(app, table, others, [("another site's link", from_a_site("document"), CROSS_SITE)]) == []
    assert refusals(app, table, table.routes("GET"), [(f"another site's {dest}", from_a_site(dest), CROSS_SITE)
                                                      for dest in ("iframe", "embed", "script")]) == []


def test_no_other_method_reaches_a_route(app, table):
    """Not even with every header right, on a server that would take the method further."""
    let_through = {(method, path): status
                   for method in ("HEAD", "PUT", "DELETE", "PATCH", "OPTIONS", "TRACE")
                   for status, path in [(knock(app, method, path, {"Origin": app.origin}).status, path)
                                        for _, path in table.routes()] if status < 400}
    assert let_through == {} and table.reached == []


ODD_PATHS = ("/%61pi/{0}", "/api%2F{0}", "//api/{0}", "///api/{0}", "/./api/{0}", "/api/../api/{0}", "/x/../api/{0}",
             "/api//{0}", "/API/{0}", "/api/{0}/", "/api/{0}%00", "/api/{0};x", "/\\api/{0}",
             "http://127.0.0.1/api/{0}", "/api/message/1/body/../../../{0}", "/%2e/api/{0}", "/next/../api/{0}",
             "/m/../api/{0}", "/brief/../api/{0}", "/assets/../api/{0}")


def last_word_of_a_route(table, method):
    return next(p.removeprefix("/api/") for _, p in table.routes(method) if p.count("/") == 2 and p.startswith("/api/"))


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_a_path_written_oddly_gets_no_further_from_a_typed_url(app, table, method):
    """The perimeter judges a path as it reads it: what it doesn't take for the API must not then run as the API."""
    for path in ODD_PATHS:
        knock(app, method, path.format(last_word_of_a_route(table, method)), {"Sec-Fetch-Site": None, "X-CSRF-Token": None, "Origin": None})
    assert [hit for hit in table.reached if hit[1].startswith("/api")] == []


def test_what_no_route_matches_is_refused_just_the_same(app, table):
    """What's wrong with a request is wrong before anyone asks whether a route takes it."""
    assert refusals(app, table, [("GET", "/api/no-such-route"), ("GET", "/no-such-page")], wrong_for_any_request(app.port)) == []
    assert refusals(app, table, [("GET", "/api/no-such-route")], wrong_for_an_api_read()) == []
    assert refusals(app, table, [(method, "/api/no-such-route") for method in ("POST", "PUT", "DELETE")],
                    [*wrong_for_any_request(app.port), *wrong_for_a_post()]) == []
    assert knock(app, "GET", "/api/no-such-route", {}).status == 404
    assert knock(app, "POST", "/api/no-such-route", {}).status == 404
    assert knock(app, "PUT", "/api/no-such-route", {"Origin": app.origin}).status == 501


def test_the_app_holds_only_its_routers_routes_and_runs_the_perimeter(monkeypatch):
    """A route on the FastAPI app itself wouldn't be in what this walks, or go through what a Route does. What no route matches is
    the app's own answer, which nothing but the perimeter runs ahead of."""
    monkeypatch.setattr(front.assistant, "use", lambda kind: None)
    monkeypatch.setattr(front.mailbox, "offer", lambda kind: None)
    asgi = front.build()
    held = [context.original_route for context in iter_route_contexts(asgi.routes)]
    assert held == route_tables.every_route() and all(isinstance(route, Route) for route in held)
    assert [m.cls.__name__ for m in asgi.user_middleware] == ["Perimeter"]
    assert asgi.router.default is http_app._no_route
