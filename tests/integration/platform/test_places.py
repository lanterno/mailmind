"""The places the page's URLs name, as a browser asks for them: a batch the page sends (`POST /api/places`), what it refuses, and
the page at `/p/<id>` holding the route the id names, escaped for the attribute it sits in."""
import html
import json
import re
from datetime import timedelta

import pytest

import problems
from mailmind.http import shared
from mailmind.platform import perimeter
from mailmind.platform.db import places
from mailmind.platform.db.uow import UnitOfWork

FOLDER = {"source": {"kind": "folder", "account": "ada@example.test", "folder": "Lists/Yarn & Wool"}, "opened": None}
TRICKY = {"source": {"kind": "search", "q": 'subject:"a<b>" & c\'s </head><script>x</script>', "account": ""}, "opened": None}
A, B = "a" * 22, "Bb-_" * 4
META = re.compile(r'<meta name="mailmind-place" content="([^"]*)">')


def held(r):
    """The route the page holds, as the page reads its meta: None for none."""
    assert r.status == 200 and r.headers["cache-control"] == "no-store"
    assert r.headers["content-security-policy"] == perimeter.page_policy()
    found = META.findall(r.body.decode())
    assert len(found) == 1
    return json.loads(html.unescape(found[0])) if found[0] else None


def used_at(place):
    with UnitOfWork().read() as c:
        return c.execute("SELECT used_at FROM places WHERE id=?", (place,)).fetchone()[0]


def test_a_place_sent_opens_as_the_page_holding_its_route(app):
    r = app.post("/api/places", {"places": [{"id": A, "route": FOLDER}, {"id": B, "route": TRICKY}]})
    assert (r.status, r.json) == (200, {"ok": True})
    assert held(app.get(f"/p/{A}")) == FOLDER
    assert held(app.get(f"/p/{B}")) == TRICKY


def test_a_routes_text_is_escaped_for_its_attribute_and_never_read_as_the_token_or_the_build(app):
    sneaky = {"source": {"kind": "search", "q": '"><script src=/x.js></script> __CSRF_TOKEN__ __BUILD__ __PLACE__ &amp;',
                         "account": ""}, "opened": None}
    app.post("/api/places", {"places": [{"id": A, "route": sneaky}]})
    text = app.get(f"/p/{A}").body.decode()
    attribute = META.search(text).group(1)
    for raw in ('"', "<", ">"):
        assert raw not in attribute
    assert "&quot;&gt;&lt;script src=/x.js&gt;&lt;/script&gt;" in attribute and "&amp;amp;" in attribute
    assert "<script src=/x.js>" not in text and "__CSRF_TOKEN__ __BUILD__ __PLACE__" in html.unescape(attribute)
    assert held(app.get(f"/p/{A}")) == sneaky


def test_the_page_anywhere_else_holds_no_place(app):
    app.post("/api/places", {"places": [{"id": A, "route": FOLDER}]})
    for path in ("/", "/all", f"/m/ada%40example.test/INBOX?p={A}", "/search?q=x"):
        assert held(app.get(path)) is None


@pytest.mark.parametrize("place", ["c" * 22, "short", "a" * 40, "%2e%2e", "a%22" * 6], ids=["unknown", "short", "long", "dots", "quote"])
def test_an_id_that_names_no_place_is_the_page_with_none(app, place):
    app.post("/api/places", {"places": [{"id": A, "route": FOLDER}]})
    assert held(app.get(f"/p/{place}")) is None


def test_a_place_pruned_since_is_the_page_with_none(app, monkeypatch):
    monkeypatch.setattr(places, "KEPT", 1)
    app.post("/api/places", {"places": [{"id": A, "route": FOLDER}]})
    app.clock.shift(timedelta(seconds=1))
    app.post("/api/places", {"places": [{"id": B, "route": TRICKY}]})
    assert held(app.get(f"/p/{A}")) is None and held(app.get(f"/p/{B}")) == TRICKY


def test_opening_a_place_marks_it_used(app):
    app.post("/api/places", {"places": [{"id": A, "route": FOLDER}]})
    sent = used_at(A)
    app.clock.shift(timedelta(days=2))
    app.get(f"/p/{A}")
    assert used_at(A) > sent


@pytest.mark.parametrize("body, said", [
    ({"places": [{"id": "x", "route": FOLDER}]}, "a place's id is 16 to 32 letters, digits, - or _"),
    ({"places": [{"id": A, "route": FOLDER}, {"id": "a/b" * 8, "route": FOLDER}]}, "a place's id is 16 to 32 letters, digits, - or _"),
    ({"places": [{"id": A, "route": {"q": "x" * places.MOST_BYTES}}]}, f"a place's route is at most {places.MOST_BYTES} bytes"),
    ({"places": [{"id": f"place-{n:011d}", "route": FOLDER} for n in range(places.MOST_AT_ONCE + 1)]},
     f"at most {places.MOST_AT_ONCE} places at once"),
], ids=["id", "one-id-of-two", "too-long", "too-many"])
def test_what_cant_be_kept_is_refused_and_none_of_the_batch_is_kept(app, body, said):
    r = app.post("/api/places", body)
    assert (r.status, r.json) == (400, problems.refused(said))
    assert held(app.get(f"/p/{A}")) is None


@pytest.mark.parametrize("body", [{}, {"places": {"id": A, "route": FOLDER}}, {"places": [{"id": A}]}, {"places": [{"route": FOLDER}]},
                                  {"places": [{"id": A, "route": [FOLDER]}]}, {"places": [{"id": A, "route": "/m/ada"}]},
                                  {"places": [{"id": 7, "route": FOLDER}]}, {"places": [A]}],
                         ids=["none", "not-a-list", "no-route", "no-id", "route-a-list", "route-text", "id-a-number", "bare-id"])
def test_a_batch_that_isnt_one_is_the_bad_request(app, body):
    r = app.post("/api/places", body)
    assert (r.status, r.json) == (400, problems.refused())


def test_a_batch_at_its_size_is_kept_whole(app):
    batch = [{"id": f"place-{n:011d}", "route": FOLDER} for n in range(places.MOST_AT_ONCE)]
    assert app.post("/api/places", {"places": batch}).status == 200
    assert all(held(app.get(f"/p/{p['id']}")) == FOLDER for p in batch[::16])


def test_a_place_is_kept_only_for_the_page_holding_this_runs_token(app):
    body = {"places": [{"id": A, "route": FOLDER}]}
    assert app.post("/api/places", body, headers={"X-CSRF-Token": None}).status == 403
    assert app.post("/api/places", body, headers={"X-CSRF-Token": "not-the-token"}).status == 403
    assert app.post("/api/places", body, headers={"Sec-Fetch-Site": "cross-site"}).status == 403
    assert held(app.get(f"/p/{A}")) is None


def test_sending_places_or_opening_one_isnt_someone_here(app, monkeypatch):
    """A list a hidden tab draws again sends the places its links name, and a tab left open reloads."""
    seen = []
    monkeypatch.setattr(shared, "_someone_here", lambda: seen.append(True))
    assert app.post("/api/places", {"places": [{"id": A, "route": FOLDER}]}).status == 200
    assert app.get(f"/p/{A}", headers={"Sec-Fetch-Site": "none"}).status == 200
    assert seen == []
