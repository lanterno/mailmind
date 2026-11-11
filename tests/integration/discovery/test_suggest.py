"""What the search box offers as you type, through its route over the stored mail: a sender and a domain finished from who has
written, a search finding what was finished, and an answer to any text or none. What is read for which word is
tests/unit/discovery/test_suggest.py."""
import urllib.parse


def offered(app, box):
    r = app.get(f"/api/suggest?q={urllib.parse.quote(box)}")
    assert r.status == 200 and r.json["ok"], r.body
    return r.json["items"]


# ── the route ───────────────────────────────────────────────────────────────────────────────────

def test_it_finishes_a_senders_address_from_who_has_written(app):
    items = offered(app, "lin")
    assert {"value": "from:lin@example.test ", "label": "Lin Mori", "detail": "lin@example.test", "complete": True} in items


def test_it_finishes_a_domain(app):
    assert "from:loom.example.test " in [i["value"] for i in offered(app, "from:loom")]


def test_a_finished_sender_finds_their_mail(app):
    value = next(i["value"] for i in offered(app, "lin") if i["value"].startswith("from:lin@"))
    found = app.get(f"/api/search?scope=all&q={urllib.parse.quote(value)}").json
    assert found["items"] and {i["from_addr"] for i in found["items"]} == {"lin@example.test"}


def test_a_finished_domain_finds_its_mail(app):
    value = next(i["value"] for i in offered(app, "from:loom") if i["detail"] == "everyone at this domain")
    found = app.get(f"/api/search?scope=all&q={urllib.parse.quote(value)}").json
    assert {i["from_addr"] for i in found["items"]} == {"news@loom.example.test"}


def test_it_answers_any_text(app):
    for q in ('"', "from:\"", "'", "-", "-:", ":", "::", "from::", "is:%", "%", "_", "\\", "é", "日本語", "x" * 4000, "\x00"):
        r = app.get(f"/api/suggest?q={urllib.parse.quote(q)}")
        assert r.status == 200 and r.json["ok"], (q, r.body)


def test_with_no_q_it_offers_nothing(app):
    assert app.get("/api/suggest").json == {"ok": True, "items": []}
