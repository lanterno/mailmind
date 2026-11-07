"""A failure says what went wrong in words of its own, and never what the exception said: an IMAP server's refusal, an SMTP
reply or an API error can hold a login, a path or a key, and a mail server is someone else's to word. Here each is forced
to carry a secret, and no answer may hold it.

test_hardening.py has one read and test_http_bridge.py the bridge's own 500. What the log may hold is logging's to say.
"""
import imaplib
import json
import sqlite3
from datetime import timedelta

import pytest

from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.bootstrap import web
from mailmind.platform import profile, webroot

SECRET = "hunter2-secret-token"
SEEN = "ada-inbox-05-receipt.eml"
SHAPED = {("GET", "place"): "/brief/needs", ("GET", "moved"): "/next/brief/needs", ("GET", "asset"): f"{webroot.PREFIX}styles/fonts.css",
          ("GET", "attachment"): "/api/message/{pk}/attachment/1", ("GET", "body"): "/api/message/{pk}/body",
          ("POST", "change"): "/api/accounts/test"}
# What never ends, or isn't an answer about anything: the page and its files, which read no mail.
NOT_WALKED = {("GET", "/api/events"), ("GET", "/")}
QUERY = ("?account={account}&email={account}&folder=INBOX&addr=lin@example.test&q=lunch&thread_id=t&kind=all&refresh=1&format=eml"
         "&days=7&limit=5")


def every_route(pk):
    table = web.H.routes
    walk = {r for r in table._exact} | {(method, path.format(pk=pk)) for (method, _), path in SHAPED.items()}
    return sorted(walk - NOT_WALKED)


def body_for(pk, **more):
    """A body with something for any route to go on, so that as many as can get as far as a server."""
    return {"pk": pk, "pks": [pk], "account": w.ADA, "email": w.ADA, "folder": "INBOX", "seen": True, "flagged": True,
            "id": 1, "thread_id": "t", "to": ["lin@example.test"], "subject": "Hi", "text": "Hello", "mode": "new", "dry_run": False,
            "token": "lin@example.test", "verdict": "keep", "cmd": "archive what lin sent", "question": "what did lin say",
            "q": "lunch", "preset": "tomorrow", "from_addr": "lin@example.test", "do_unsubscribe": False, "in_reply_to_pk": pk,
            "tone": "shorter", "instruction": "shorter", "plan": "none", "draft_id": 1, "name": "v", "action": "keep", **more}


def holds_it(r, *secrets):
    text = json.dumps(r.body, default=repr) if isinstance(r.body, (dict, list)) else bytes(r.body).decode("utf-8", "replace")
    text += json.dumps(r.headers)
    return [s for s in secrets if s in text]


class Raising:
    """The route table with each route raising what it's given."""

    def __init__(self, real, make):
        self.real, self.make = real, make

    def find(self, method, path):
        if self.real.find(method, path) is None:
            return None

        def boom(req):
            raise self.make()
        return boom


def walk_for_leaks(app, routes, body, secrets):
    leaks = {}
    query = QUERY.format(account=w.ADA)
    for method, path in routes:
        r = app.post(path, body) if method == "POST" else app.get(path + query)
        found = holds_it(r, *secrets)
        if found:
            leaks[method, path] = (r.status, r.body if isinstance(r.body, (dict, list)) else r.body[:200])
    return leaks


# ── whatever a route raises ──────────────────────────────────────────────────────────────────────

RAISED = {"RuntimeError": lambda: RuntimeError(SECRET), "KeyError": lambda: KeyError(SECRET), "OSError": lambda: OSError(13, SECRET, SECRET),
          "sqlite3": lambda: sqlite3.OperationalError(SECRET), "IMAP refused": lambda: imaplib.IMAP4.error(f"NO {SECRET}"),
          "IMAP lost": lambda: imaplib.IMAP4.abort(SECRET), "a type error": lambda: TypeError(SECRET),
          "a value error": lambda: ValueError(SECRET), "read-only": lambda: profile.ReadOnlyError(SECRET)}


@pytest.mark.parametrize("what", sorted(RAISED))
def test_whatever_a_route_raises_its_answer_says_only_that_it_failed(app, monkeypatch, what):
    pk = 1
    routes = every_route(pk)
    assert ("POST", "/api/send") in routes and ("GET", "/api/digest") in routes
    monkeypatch.setattr(web.H, "routes", Raising(web.H.routes, RAISED[what]))
    answers = {}
    for method, path in routes:
        r = app.post(path, {}) if method == "POST" else app.get(path)
        assert not holds_it(r, SECRET), (method, path, r.body)
        answers.setdefault((r.status, json.dumps(r.body)), (method, path))
    fixed = {'{"ok": false, "error": "request failed"}', '{"ok": false, "error": "bad request"}',
             '{"ok": false, "error": "MailMind is running read-only", "read_only": true}'}
    assert {said for _, said in answers} <= fixed, answers


# ── a server's own words ─────────────────────────────────────────────────────────────────────────

MAILBOX_COMMANDS = ("LIST", "SELECT", "EXAMINE", "STATUS", "UID SEARCH", "UID FETCH", "UID STORE", "UID MOVE", "UID COPY", "UID EXPUNGE",
                    "APPEND", "NOOP")
# Each account's result in these two still says what the server said: pinned below.
REPEAT_A_SERVER = {("POST", "/api/reconcile"), ("POST", "/api/sweep")}


@pytest.mark.parametrize("refuses", ["login", "every mailbox command", "only a move"])
def test_what_an_imap_server_says_stays_out_of_every_answer(app, world, refuses, monkeypatch):
    """The account's password goes on the wire at each login, so nothing of it may come back either. A refused login is
    held back, which would leave all routes but the first with nothing a server said to hold, so here it isn't."""
    from mailmind.bootstrap import accounts
    monkeypatch.setattr(accounts.logins, "held", lambda acct: False)
    pk = world.pk(SEEN)
    commands = {"login": ("LOGIN",), "every mailbox command": MAILBOX_COMMANDS, "only a move": ("UID MOVE", "UID COPY")}[refuses]
    for command in commands:
        world.server.fail(command, f"NO [AUTHENTICATIONFAILED] {SECRET}", times=None)
    password = world.server.accounts[w.ADA].password
    routes = [r for r in every_route(pk) if r not in REPEAT_A_SERVER]
    first = walk_for_leaks(app, routes, body_for(pk), (SECRET, password))
    again = walk_for_leaks(app, routes, body_for(pk), (SECRET, password))     # what the first pass left behind, read back
    assert {**first, **again} == {}


def test_what_an_smtp_server_says_stays_out_of_every_answer(app, world):
    from mailmind.bootstrap.compose import outbox
    pk = world.pk(SEEN)
    world.smtp.fail("MAIL", f"550 5.7.1 {SECRET}", times=None)
    assert app.post("/api/send", {"account": w.ADA, "mode": "new", "to": ["lin@example.test"], "subject": "Hi", "text": "Hello",
                                  "idempotency_key": "k"}).json["ok"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()
    assert walk_for_leaks(app, [("GET", "/api/outbox"), ("GET", "/api/pending"), ("GET", "/api/account-health"), ("GET", "/api/stats"),
                                ("GET", "/api/debug/jobs"), ("GET", "/api/audit")], {}, (SECRET,)) == {}
    assert walk_for_leaks(app, [("POST", "/api/outbox/retry")], body_for(pk), (SECRET,)) == {}


def test_what_an_smtp_server_says_at_login_stays_out_too(app, world):
    from mailmind.bootstrap.compose import outbox
    world.smtp.fail("AUTH", f"535 5.7.8 {SECRET}", times=None)
    app.post("/api/send", {"account": w.ADA, "mode": "new", "to": ["lin@example.test"], "subject": "Hi", "text": "Hello",
                           "idempotency_key": "k"})
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()
    assert walk_for_leaks(app, [("GET", "/api/outbox"), ("GET", "/api/account-health")], {}, (SECRET,)) == {}


# ── what the Claude API says ─────────────────────────────────────────────────────────────────────

def said_by_the_api(ai, status, kind):
    """It may quote a key back, or part of a prompt."""
    ai.responder = lambda req: fa.error(status, kind, f"sk-ant-{SECRET}")


@pytest.mark.parametrize("status, kind", [(400, "invalid_request_error"), (401, "authentication_error")])
def test_what_the_claude_api_says_stays_out_of_every_answer_but_the_digests(app, world, ai, status, kind):
    pk = world.pk(SEEN)
    said_by_the_api(ai, status, kind)
    routes = [r for r in every_route(pk) if "/ai/" in r[1] or r[1] in ("/api/briefing", "/api/briefing/refresh", "/api/briefing/since",
                                                                       "/api/review", "/api/review/refresh")]
    assert len(routes) > 8
    assert walk_for_leaks(app, routes + [("GET", "/api/debug/ai")], body_for(pk), (SECRET,)) == {}


def digest_after_a_failed_call(app, ai, status, kind):
    said_by_the_api(ai, status, kind)
    app.post("/api/review", {})
    return app.get("/api/digest")


@pytest.mark.parametrize("status, kind", [(401, "authentication_error"), (429, "rate_limit_error")])
def test_the_digest_says_why_a_call_failed_in_words_of_its_own(app, world, ai, status, kind):
    r = digest_after_a_failed_call(app, ai, status, kind)
    assert r.json["ai"]["last_ok"] is False and r.json["ai"]["last_error"]
    assert not holds_it(r, SECRET)


@pytest.mark.parametrize("path", ["/api/reconcile", "/api/sweep"])
def test_a_sweep_or_a_reconcile_says_an_account_failed_in_words_of_its_own(app, world, path):
    world.server.fail("LOGIN", f"NO [AUTHENTICATIONFAILED] {SECRET}", times=None)
    assert not holds_it(app.post(path, body_for(world.pk(SEEN))), SECRET)


@pytest.mark.parametrize("status, kind", [(400, "invalid_request_error"), (500, "api_error")])
def test_the_digest_never_repeats_what_the_claude_api_said(app, world, ai, status, kind):
    assert not holds_it(digest_after_a_failed_call(app, ai, status, kind), SECRET)
