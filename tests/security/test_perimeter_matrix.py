"""The perimeter matrix: every route the app has, through each front door, against each way a request can be wrong. The walk
reads the route table, so a route added tomorrow is in it. test_perimeter.py pins each check on its own, and
test_http_post_guards.py a few routes end to end.

Every route is swapped for one that only says it ran. So the walk can knock on all of them, the event stream and a send
included, with nothing happening, and an honest request reaching each shows the refusals are the perimeter's own.
"""
import app_client
import pytest

from mailmind.bootstrap import front, web
from mailmind.platform import webroot

# A route that matches by shape names no path, so each has one here. A new one fails test_every_shape_has_a_path_here.
SHAPED = {("GET", "place"): "/brief/needs", ("GET", "moved"): "/next/brief/needs", ("GET", "asset"): f"{webroot.PREFIX}styles/fonts.css",
          ("GET", "attachment"): "/api/message/1/attachment/1", ("GET", "body"): "/api/message/1/body", ("GET", "body_document"): "/api/message/1/body.html",
          ("POST", "change"): "/api/accounts/test"}
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
    """The route table with each route answering only that it ran. A path the real table doesn't know stays unknown."""

    def __init__(self, real):
        self.real, self.reached = real, []

    def find(self, method, path):
        if self.real.find(method, path) is None:
            return None

        def ran(req):
            self.reached.append((method, path))
            return {"ok": True, "ran": path}
        return ran

    def routes(self, method=None):
        """Every route, as (method, a path it answers)."""
        walk = sorted({*self.real._exact, *SHAPED_PATHS})
        return [r for r in walk if method in (None, r[0])]


SHAPED_PATHS = {(method, path) for (method, _), path in SHAPED.items()}


@pytest.fixture
def table(app, monkeypatch):
    spy = Table(web.H.routes)
    monkeypatch.setattr(web.H, "routes", spy)
    monkeypatch.setattr(app_client, "RECORD_API", None)     # what these answer isn't what the routes answer
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


def test_every_shape_has_a_path_here():
    """A route by shape only joins the walk through SHAPED: give a new one a path it answers."""
    table = web.H.routes
    named = {(method, route.__name__) for method, _, route in table._matching}
    assert named == set(SHAPED)
    for (method, name), path in SHAPED.items():
        assert table.find(method, path).__name__ == name, path


def test_the_walk_holds_what_the_table_holds():
    walk = Table(web.H.routes).routes()
    assert set(web.H.routes._exact) <= set(walk) and len(walk) == len(web.H.routes._exact) + len(SHAPED)
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
    """Not even with every header right, through a door that would take the method further."""
    let_through = {(method, path): status
                   for method in ("HEAD", "PUT", "DELETE", "PATCH", "OPTIONS", "TRACE")
                   for status, path in [(knock(app, method, path, {"Origin": app.origin}).status, path)
                                        for _, path in table.routes()] if status < 400}
    assert let_through == {} and table.reached == []


ODD_PATHS = ("/%61pi/digest", "/api%2Fdigest", "//api/digest", "///api/digest", "/./api/digest", "/api/../api/digest", "/x/../api/digest",
             "/api//digest", "/API/digest", "/api/digest/", "/api/digest%00", "/api/digest;x", "/\\api/digest",
             "http://127.0.0.1/api/digest", "/api/message/1/body/../../../digest", "/%2e/api/digest", "/next/../api/digest",
             "/m/../api/digest", "/brief/../api/digest", "/assets/../api/digest")


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_a_path_written_oddly_gets_no_further_from_a_typed_url(app, table, method):
    """The perimeter judges a path as it reads it: what it doesn't take for the API must not then run as the API."""
    for path in ODD_PATHS:
        knock(app, method, path, {"Sec-Fetch-Site": None, "X-CSRF-Token": None, "Origin": None})
    assert [hit for hit in table.reached if hit[1].startswith("/api")] == []


def test_the_handler_answers_no_method_the_perimeter_doesnt_check():
    """A new `do_PUT` on the one handler would be a door of its own on the old server, which runs each check by hand."""
    assert sorted(name for name in dir(web.H) if name.startswith("do_")) == ["do_GET", "do_POST"]


def test_the_new_door_has_no_route_of_its_own_and_runs_the_perimeter(monkeypatch):
    """A route on the FastAPI app itself wouldn't be in the table this walks."""
    monkeypatch.setattr(front.assistant, "use", lambda kind: None)
    monkeypatch.setattr(front.mailbox, "offer", lambda kind: None)
    asgi, _ = front.build()
    assert asgi.routes == []
    assert [m.cls.__name__ for m in asgi.user_middleware] == ["Perimeter"]
