"""Every answer carries the perimeter's headers (mailmind.platform.perimeter.answer_headers),
whoever writes it: a route, the perimeter's own refusal, or http.server
answering a method no route takes. The golden files pin the values route by route."""
from mailmind.platform import perimeter


def carries_the_perimeters_headers(resp, path):
    want = {k.lower(): v for k, v in perimeter.answer_headers(path)}
    return {k: resp.headers.get(k) for k in want} == want


def test_a_routes_answer(app):
    r = app.get("/api/outbox")
    assert r.status == 200 and carries_the_perimeters_headers(r, "/api/outbox")
    r = app.get("/")
    assert r.status == 200 and carries_the_perimeters_headers(r, "/")


def test_a_refusal(app):
    r = app.get("/api/outbox", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status == 403 and carries_the_perimeters_headers(r, "/api/outbox")
    r = app.post("/api/seen", {}, headers={"X-CSRF-Token": None})
    assert r.status == 403 and carries_the_perimeters_headers(r, "/api/seen")


def test_an_error_http_server_writes_itself(app):
    r = app.request("PUT", "/api/seen", headers={"Origin": app.origin})
    assert r.status == 501 and carries_the_perimeters_headers(r, "/api/seen")
