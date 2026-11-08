"""What every POST must get past before a route runs: the body-size cap, the rebind check, the
CSRF token and this server's Origin. Malformed input gets a clean refusal. The golden file is in
tests/golden/http_post_guards/.
"""
import http.client
import json

import pytest

import app_client
import problems
from fixtures import world as w

# The routes that send mail or act on queued sends, where a POST the page didn't make costs most.
SENDS = ("/api/send", "/api/draft", "/api/invite/rsvp", "/api/outbox/retry", "/api/outbox/cancel",
          "/api/outbox/sendnow", "/api/cleanup/execute")


def raw_post(app, path, body=b"{}", headers=()):
    """A POST whose headers the test writes itself, Content-Length included."""
    h = {"Host": f"127.0.0.1:{app.port}", "Origin": app.origin, "X-CSRF-Token": app_client.CSRF,
         "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json", "Content-Length": str(len(body)),
         **dict(headers)}
    conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=30)
    try:
        conn.putrequest("POST", path, skip_host=True, skip_accept_encoding=True)
        for k, v in h.items():
            conn.putheader(k, v)
        conn.endheaders()
        conn.send(body)
        r = conn.getresponse()
        return r.status, r.read().decode()
    finally:
        conn.close()


def test_the_guards(app, golden, world):
    pk = world.pk("ada-inbox-02-lunch-confirm.eml")
    seen = {"pk": pk, "seen": True}
    golden.record("POST /api/seen without the CSRF token", app.post("/api/seen", seen, headers={"X-CSRF-Token": None}))
    golden.record("POST /api/seen with a wrong CSRF token", app.post("/api/seen", seen, headers={"X-CSRF-Token": "nope"}))
    golden.record("POST /api/seen from a rebinding host", app.post("/api/seen", seen, headers={"Host": "evil.example.test"}))
    golden.record("POST /api/seen with a foreign Origin", app.post("/api/seen", seen,
                                                                   headers={"Origin": "http://evil.example.test"}))
    golden.note("the message is still unread", world.row("ada-inbox-02-lunch-confirm.eml")["seen"])
    golden.record("POST /api/seen without an Origin", app.post("/api/seen", seen, headers={"Origin": None}))
    for path in SENDS:
        golden.record(f"POST {path} without an Origin", app.post(path, {}, headers={"Origin": None}))
    golden.record("POST /api/no-such-route", app.post("/api/no-such-route", {}))
    golden.record("POST /api/accounts/no-such-action", app.post("/api/accounts/no-such-action", {}))


def test_the_size_cap_and_malformed_bodies(app, golden):
    for label, path, length in (("a control route over 1 MB", "/api/seen", (1 << 20) + 1),
                                ("the send route over 38 MB", "/api/send", (38 << 20) + 1)):
        status, text = raw_post(app, path, headers={"Content-Length": str(length)})
        golden.note(f"{label}: {path}", {"status": status, "body": text})
    status, text = raw_post(app, "/api/seen", b"not json")
    golden.note("a body that isn't JSON", {"status": status, "body": text})
    status, text = raw_post(app, "/api/seen", b"[1, 2]")
    golden.note("a JSON body that isn't an object", {"status": status, "body": text})
    golden.record("an id that isn't a number", app.post("/api/outbox/retry", {"id": "x"}))
    golden.record("a missing id", app.post("/api/outbox/cancel", {}))


@pytest.mark.parametrize("length", ["-1", "lots"])
def test_a_length_that_isnt_one_is_refused_before_any_route_runs(app, world, length):
    """A Content-Length that isn't a length is broken HTTP, so uvicorn answers 400 before the app
    sees the request. The route never runs: it would answer 200.

    No body follows the headers. uvicorn closes the connection as it answers, and a body it never
    read would make that close a reset, which can cut its answer off before the test reads it."""
    since = world.mark()
    status, _ = raw_post(app, "/api/seen", b"", headers={"Content-Length": length})
    assert status == 400
    assert world.writes(since) == []


def test_a_guarded_post_changes_nothing_on_the_server(app, world):
    since = world.mark()
    app.post("/api/trash", {"pk": world.pk("ada-inbox-05-receipt.eml")}, headers={"X-CSRF-Token": None})
    app.post("/api/archive", {"pk": world.pk("ada-inbox-05-receipt.eml")}, headers={"Host": "evil.example.test"})
    assert world.writes(since) == []
    assert world.where("ada-inbox-05-receipt.eml")["server"] == [[w.ADA, "INBOX", 5, []]]


def test_only_a_post_that_gets_past_the_guards_counts_as_someone_at_the_screen(app, world, monkeypatch):
    """Presence keeps the AI pre-drafting replies, so another site's refused POSTs mustn't."""
    import time
    from mailmind.bootstrap import assistant
    governor = assistant.governor()
    monkeypatch.setattr(governor, "_last_seen", 0.0)
    seen = {"pk": world.pk("ada-inbox-02-lunch-confirm.eml"), "seen": True}
    assert app.post("/api/seen", seen, headers={"X-CSRF-Token": None}).status == 403
    assert app.post("/api/seen", seen, headers={"Host": "evil.example.test"}).status == 403
    assert app.post("/api/send", {}, headers={"Origin": None}).status == 403
    assert governor._last_seen == 0.0
    assert app.post("/api/seen", seen).status == 200
    assert governor._last_seen == time.time()


def test_a_post_is_refused_before_its_body_is_read(app):
    """The perimeter runs ahead of any route, so a refused POST costs no read."""
    conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=5)
    try:
        conn.putrequest("POST", "/api/seen", skip_host=True, skip_accept_encoding=True)
        for k, v in {"Host": f"127.0.0.1:{app.port}", "Origin": app.origin, "Sec-Fetch-Site": "same-origin",
                     "Content-Type": "application/json", "Content-Length": "1000"}.items():
            conn.putheader(k, v)
        conn.endheaders()
        r = conn.getresponse()
        assert (r.status, json.loads(r.read())) == (403, problems.blank(403, "bad or missing CSRF token"))
    finally:
        conn.close()
