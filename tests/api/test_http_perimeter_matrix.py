"""The perimeter matrix: every route the app has, and what no route matches, against each way a request can be wrong, on the
served server, so both servers are held to one set. The walk reads the route table (support/route_tables.py), so a route added
tomorrow is in it. tests/security/test_perimeter.py and the Rust server's tests/perimeter.rs pin each check on its own, and
test_http_post_guards.py a few routes end to end.

The routes are the server's own, so a refusal is told from a route's answer by what it says: the perimeter's 403 with its own
words, as a problem document of no type of the app's. An honest request reaching each route shows those words are the
perimeter's, and none of the refused ones changes anything on the mail server.
"""
import http.client
import json

import problems
import route_tables
from fixtures import tiny_build

BAD_HOST = "bad host/origin"
CROSS_SITE = "cross-site request"
NOT_THE_PAGE = "same-origin request required"
NO_ORIGIN = "origin required"
BAD_TOKEN = "bad or missing CSRF token"
PERIMETER = {BAD_HOST, CROSS_SITE, NOT_THE_PAGE, NO_ORIGIN, BAD_TOKEN}

# A route the walk names by a value that is there, so the route's own "not found" can't be taken for no route at all.
THERE = {"/assets/1": "/" + tiny_build.SCRIPT}


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
            ("no Origin", {"Origin": None}, NO_ORIGIN),
            ("no token", {"X-CSRF-Token": None}, BAD_TOKEN),
            ("an empty token", {"X-CSRF-Token": ""}, BAD_TOKEN),
            ("a wrong token", {"X-CSRF-Token": "not-the-token"}, BAD_TOKEN)]


def wrong_for_an_api_read():
    return [("a request that says nothing of where it came from", {"Sec-Fetch-Site": None}, NOT_THE_PAGE),
            ("a typed URL", {"Sec-Fetch-Site": "none"}, NOT_THE_PAGE)]


def routes(method=None):
    """Every route, as (method, a path it answers)."""
    return sorted((m, THERE.get(p, p)) for m, p in route_tables.paths() if method in (None, m))


class Answer:
    def __init__(self, status, content_type, body):
        self.status, self.content_type, self.body = status, content_type, body

    def problem(self):
        """The problem document it is, or None."""
        if self.content_type != problems.CONTENT_TYPE:
            return None
        try:
            return json.loads(self.body)
        except ValueError:
            return None

    def refused_by_the_perimeter(self):
        doc = self.problem()
        return self.status == 403 and doc is not None and doc.get("type") == "about:blank" and doc.get("detail") in PERIMETER

    def no_route(self):
        """What a server answers where no route matches: a 404 of no type of the app's, or the line of text saying so."""
        doc = self.problem()
        return self.status == 404 and (self.body == b"not found" if doc is None else doc == problems.blank(404, "not found"))

    def from_a_route(self):
        """An answer only a route gives: JSON, or a problem of one of the app's own types."""
        doc = self.problem()
        return self.content_type.startswith("application/json") or (doc is not None and doc.get("type") != "about:blank")

    def __repr__(self):
        return f"{self.status} {self.content_type} {self.body[:80]!r}"


def knock(app, method, path, headers):
    """A request as the page's would be, with `headers` over it (None takes one away), and what it answered. The body of an
    event stream, which goes on until the server stops, is left unread."""
    sent = {"Host": f"127.0.0.1:{app.port}", "Sec-Fetch-Site": "same-origin", "X-CSRF-Token": app.csrf}
    body = None
    if method == "POST":
        sent.update({"Origin": app.origin, "Content-Type": "application/json"})
        body = b"{}"
    for name, value in headers.items():
        if value is None:
            sent.pop(name, None)
        else:
            sent[name] = value
    conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=60)
    try:
        conn.request(method, path, body=body, headers=sent)
        r = conn.getresponse()
        content_type = r.getheader("content-type") or ""
        return Answer(r.status, content_type, b"" if content_type.startswith("text/event-stream") else r.read())
    finally:
        conn.close()


def refusals(app, walk, cases):
    """What didn't answer as it should: each route, in each way it can be wrong."""
    wrong = []
    for method, path in walk:
        for what, headers, error in cases:
            r = knock(app, method, path, headers)
            if (r.status, r.problem(), r.content_type) != (403, problems.blank(403, error), problems.CONTENT_TYPE):
                wrong.append((method, path, what, r))
    return wrong


def test_an_honest_request_reaches_every_route(app):
    """So that a refusal below is the perimeter refusing, and not this walk missing the door."""
    walk = routes()
    assert {("GET", "/"), ("GET", "/api/events"), ("POST", "/api/send"), ("POST", "/api/cleanup/execute"),
            ("POST", "/api/ai/do-confirm")} <= set(walk)
    missed = [(method, path, r) for method, path in walk
              if (r := knock(app, method, path, {})).refused_by_the_perimeter() or r.no_route() or r.status >= 500]
    assert missed == []


def test_every_read_is_refused_from_anywhere_else(app, world):
    since = world.mark()
    assert refusals(app, routes("GET"), wrong_for_any_request(app.port)) == []
    assert world.writes(since) == []


def test_every_read_of_the_api_is_refused_unless_the_page_asked(app):
    api = [r for r in routes("GET") if r[1].startswith("/api/")]
    assert refusals(app, api, wrong_for_an_api_read()) == []


def test_every_post_is_refused_from_anywhere_else(app, world):
    since = world.mark()
    assert refusals(app, routes("POST"), wrong_for_any_request(app.port)) == []
    assert world.writes(since) == []


def test_every_post_is_refused_without_its_origin_and_its_token(app, world):
    since = world.mark()
    assert refusals(app, routes("POST"), wrong_for_a_post()) == []
    assert world.writes(since) == []


def test_another_sites_link_opens_the_page_and_nothing_else(app):
    """A top-level visit to `/` is the one thing another site may start."""
    def from_a_site(dest):
        return {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": dest}
    page = knock(app, "GET", "/", from_a_site("document"))
    assert (page.status, page.content_type.split(";")[0]) == (200, "text/html")
    others = [r for r in routes("GET") if r[1] != "/"]
    assert refusals(app, others, [("another site's link", from_a_site("document"), CROSS_SITE)]) == []
    assert refusals(app, routes("GET"), [(f"another site's {dest}", from_a_site(dest), CROSS_SITE)
                                         for dest in ("iframe", "embed", "script")]) == []


def test_no_other_method_reaches_a_route(app, world):
    """Not even with every header right, on a server that would take the method further."""
    since = world.mark()
    let_through = {(method, path): r for method in ("HEAD", "PUT", "DELETE", "PATCH", "OPTIONS", "TRACE")
                   for _, path in routes() if (r := knock(app, method, path, {"Origin": app.origin})).status < 400}
    assert let_through == {}
    assert world.writes(since) == []


ODD_PATHS = ("/%61pi/{0}", "/api%2F{0}", "//api/{0}", "///api/{0}", "/./api/{0}", "/api/../api/{0}", "/x/../api/{0}",
             "/api//{0}", "/API/{0}", "/api/{0}/", "/api/{0}%00", "/api/{0};x", "/\\api/{0}",
             "http://127.0.0.1/api/{0}", "/api/message/1/body/../../../{0}", "/%2e/api/{0}", "/next/../api/{0}",
             "/m/../api/{0}", "/brief/../api/{0}", "/assets/../api/{0}")

# A route each method's walk writes oddly, one an honest request with nothing in its body has answer with JSON, so a route that
# ran would show.
ODDLY = {"GET": "accounts", "POST": "preferences"}


def test_a_path_written_oddly_gets_no_further_from_a_typed_url(app, world):
    """The perimeter judges a path as it reads it: what it doesn't take for the API must not then run as the API."""
    since = world.mark()
    for method, word in ODDLY.items():
        assert knock(app, method, f"/api/{word}", {}).from_a_route()
    ran = [(method, path, r) for method, word in ODDLY.items() for path in (p.format(word) for p in ODD_PATHS)
           if (r := knock(app, method, path, {"Sec-Fetch-Site": None, "X-CSRF-Token": None, "Origin": None})).from_a_route()
           or r.status >= 500]
    assert ran == []
    assert world.writes(since) == []


def test_what_no_route_matches_is_refused_just_the_same(app):
    """What's wrong with a request is wrong before anyone asks whether a route takes it."""
    assert refusals(app, [("GET", "/api/no-such-route"), ("GET", "/no-such-page")], wrong_for_any_request(app.port)) == []
    assert refusals(app, [("GET", "/api/no-such-route")], wrong_for_an_api_read()) == []
    assert refusals(app, [(method, "/api/no-such-route") for method in ("POST", "PUT", "DELETE")],
                    [*wrong_for_any_request(app.port), *wrong_for_a_post()]) == []
    assert knock(app, "GET", "/api/no-such-route", {}).no_route()
    assert knock(app, "POST", "/api/no-such-route", {}).no_route()
    assert knock(app, "GET", "/no-such-page", {}).no_route()
    put = knock(app, "PUT", "/api/no-such-route", {"Origin": app.origin})
    assert (put.status, put.problem()) == (501, problems.blank(501, "not implemented"))
