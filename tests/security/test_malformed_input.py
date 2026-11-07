"""A request that doesn't read as one gets the 400 a malformed request gets, not the 500 that means a bug here."""
import pytest

from fixtures import world as w
from mailmind.bootstrap import web
from mailmind.kernel.errors import Refused
from mailmind.kernel.parsed import as_int, required_int

BAD_REQUEST = {"ok": False, "error": "bad request"}
TOO_LARGE = "99999999999999999999999"


@pytest.mark.parametrize("held, read", [(7, 7), ("7", 7), (True, 1), (1.5, 1), (2 ** 53, 2 ** 53), (-(2 ** 53), -(2 ** 53)),
                                        (2 ** 53 + 1, None), (10 ** 30, None), (float("inf"), None), (float("nan"), None),
                                        ("x", None), ("", None), (None, None), ([1], None), ({}, None)])
def test_a_number_reads_or_it_is_none(held, read):
    assert as_int(held) == read
    if read is None:
        with pytest.raises(Refused):
            required_int(held)
    else:
        assert required_int(held) == read


@pytest.mark.parametrize("path, name", [("/api/audit", "limit"), ("/api/debug/ai", "days"), ("/api/digest", "min"),
                                        ("/api/messages?account=" + w.ADA + "&folder=INBOX", "limit"),
                                        ("/api/messages?account=" + w.ADA + "&folder=INBOX", "offset"),
                                        ("/api/inboxes", "limit"), ("/api/inboxes", "offset"), ("/api/outbox", "limit"),
                                        ("/api/search?q=lunch", "limit"), ("/api/search?q=lunch", "offset"),
                                        ("/api/waiting-on-others", "limit")])
@pytest.mark.parametrize("junk", ["x", TOO_LARGE])
def test_a_number_in_a_query_string_that_wont_read(app, path, name, junk):
    r = app.get(f"{path}{'&' if '?' in path else '?'}{name}={junk}")
    assert (r.status, r.json) == (400, BAD_REQUEST)


def test_a_search_with_nothing_typed_answers_whatever_the_limit_says(app):
    r = app.get("/api/search?q=&limit=x")
    assert (r.status, r.json["ok"]) == (200, True)


@pytest.mark.parametrize("path", ["/api/message/{}/body", "/api/message/{}/attachment/1"])
@pytest.mark.parametrize("junk", ["x", TOO_LARGE])
def test_a_message_named_by_what_cant_be_a_number(app, path, junk):
    r = app.get(path.format(junk))
    assert (r.status, r.json) == (400, BAD_REQUEST)


@pytest.mark.parametrize("junk", ["x", TOO_LARGE])
def test_drafting_ahead_for_what_cant_be_a_message(app, junk):
    r = app.get(f"/api/ai/draft-ahead?pk={junk}")
    assert (r.status, r.json) == (400, {"ok": False, "error": "bad pk"})


@pytest.mark.parametrize("path", ["/api/outbox/retry", "/api/outbox/cancel", "/api/outbox/sendnow", "/api/pending/retry",
                                  "/api/pending/discard"])
@pytest.mark.parametrize("junk", ["x", 10 ** 30])
def test_a_queued_item_named_by_what_cant_be_a_number(app, path, junk):
    r = app.post(path, {"id": junk})
    assert (r.status, r.json) == (400, BAD_REQUEST)


@pytest.mark.parametrize("path, field, said", [("/api/trash", "pk", "bad request"), ("/api/ai/summarize", "pk", "bad request"),
                                               ("/api/ai/draft-reply", "pk", "bad request"), ("/api/mute", "pk", "bad request"),
                                               ("/api/unmute", "pk", "bad request"), ("/api/followups/dismiss", "pk", "bad request"),
                                               ("/api/replylater", "pk", "bad request"), ("/api/snooze", "pk", "bad request"),
                                               ("/api/unsnooze", "pk", "bad request"), ("/api/invite/rsvp", "pk", "missing message"),
                                               ("/api/draft/resume", "draft_id", "Missing draft."),
                                               ("/api/draft/resume", "pk", "Missing draft.")])
@pytest.mark.parametrize("junk", [[1], 10 ** 30])
def test_a_message_named_in_a_body_by_what_cant_be_a_number(app, path, field, said, junk):
    r = app.post(path, {field: junk, "preset": "tomorrow"})
    assert (r.status, r.json) == (400, {"ok": False, "error": said})


@pytest.mark.parametrize("path", ["/api/seen", "/api/flag", "/api/archive", "/api/junk", "/api/move", "/api/thread/archive",
                                  "/api/thread/trash"])
@pytest.mark.parametrize("junk", ["x", 10 ** 30])
def test_filing_what_cant_be_a_message_answers_as_any_refused_filing_does(app, path, junk):
    r = app.post(path, {"pk": junk, "folder": "Archive"})
    assert (r.status, r.json) == (200, {"ok": False, "msg": "bad pk"})


@pytest.mark.parametrize("junk", [[1], 10 ** 30])
def test_any_of_many_named_by_what_cant_be_a_number(app, world, junk):
    pk = world.pk("ada-inbox-02-lunch-confirm.eml")
    for path in ("/api/snooze", "/api/unsnooze"):
        r = app.post(path, {"pks": [pk, junk], "preset": "tomorrow"})
        assert (r.status, r.json) == (400, BAD_REQUEST)
    assert not world.row("ada-inbox-02-lunch-confirm.eml")["snoozed_until"]


@pytest.mark.parametrize("path", ["/api/draft", "/api/send"])
@pytest.mark.parametrize("junk", ["x", 10 ** 30])
def test_a_save_or_a_send_naming_what_cant_be_a_draft(app, path, junk):
    r = app.post(path, {"draft_id": junk, "account": w.ADA, "mode": "new", "to": ["lin@example.test"], "subject": "Hi", "text": "Hello"})
    assert (r.status, r.json) == (400, BAD_REQUEST)
    assert not app.get("/api/outbox").json["items"]


@pytest.mark.parametrize("junk", ["x", 10 ** 30])
def test_a_discard_naming_what_cant_be_a_draft(app, junk):
    r = app.post("/api/draft", {"draft_id": junk, "delete": True})
    assert (r.status, r.json) == (400, BAD_REQUEST)


@pytest.mark.parametrize("junk", ["x", 10 ** 30])
def test_a_reply_naming_what_cant_be_its_original(app, junk):
    r = app.post("/api/send", {"account": w.ADA, "mode": "reply", "in_reply_to_pk": junk, "to": ["lin@example.test"], "text": "Hello"})
    assert (r.status, r.json) == (400, {"ok": False, "error": "missing original message"})


@pytest.mark.parametrize("path, field", [("/api/ai/ask", "account"), ("/api/ai/draft-reply", "account"), ("/api/ai/summarize", "account"),
                                         ("/api/briefing", "account"), ("/api/review", "account"), ("/api/draft", "account"),
                                         ("/api/send", "account"), ("/api/reconcile", "account"), ("/api/sweep", "account"),
                                         ("/api/snooze", "account"), ("/api/unsnooze", "account"), ("/api/sync-retry", "account"),
                                         ("/api/accounts/add", "email"), ("/api/accounts/edit", "email"),
                                         ("/api/accounts/trust-cert", "email"), ("/api/ai/draft-reply", "thread_id"),
                                         ("/api/followups/dismiss", "thread_id"), ("/api/mute", "thread_id"),
                                         ("/api/unmute", "thread_id"), ("/api/replylater", "thread_id"),
                                         ("/api/snooze", "thread_id"), ("/api/unsnooze", "thread_id"),
                                         ("/api/cleanup/execute", "from_addr"), ("/api/cleanup/execute", "undo_group"),
                                         ("/api/draft", "mode"), ("/api/send", "mode"), ("/api/draft", "subject"),
                                         ("/api/send", "subject"), ("/api/draft", "text"), ("/api/send", "text"),
                                         ("/api/invite/rsvp", "decision"), ("/api/rules", "token"), ("/api/send", "idempotency_key")])
@pytest.mark.parametrize("junk", [5])
def test_a_text_field_that_holds_anything_else(app, path, field, junk):
    r = app.post(path, {"account": w.ADA, "thread_id": "t", field: junk})
    assert (r.status, r.json) == (400, BAD_REQUEST)


def test_a_text_field_may_say_nothing(app, world):
    r = app.post("/api/mute", {"account": None, "thread_id": None, "pk": world.pk("ada-inbox-02-lunch-confirm.eml")})
    assert (r.status, r.json["ok"]) == (200, True)


@pytest.mark.parametrize("path", ["/api/draft", "/api/send"])
@pytest.mark.parametrize("junk", [[1], {"a": 1}, 5])
def test_a_file_named_by_anything_but_text(app, path, junk):
    r = app.post(path, {"account": w.ADA, "mode": "new", "to": ["lin@example.test"], "subject": "Hi", "text": "Hello",
                        "attachments": [{"filename": junk, "b64": "aGk="}]})
    assert (r.status, r.json) == (400, {"ok": False, "error": "bad attachment"})


@pytest.mark.parametrize("verb, field", [("delete", "id"), ("create", "name"), ("create", "q")])
@pytest.mark.parametrize("junk", [[], [1], {"a": 1}, 5])
def test_a_saved_view_named_by_anything_but_text(app, verb, field, junk):
    before = app.get("/api/views").json
    r = app.post("/api/views", {"verb": verb, "name": "Lunch", "q": "lunch", field: junk})
    assert (r.status, r.json) == (400, BAD_REQUEST)
    assert app.get("/api/views").json == before


@pytest.mark.parametrize("path", ["/api/folders", "/api/stats"])
def test_asking_after_an_account_there_is_no_such_of(app, path):
    r = app.get(f"{path}?account=nobody@example.test")
    assert (r.status, r.json) == (400, {"ok": False, "error": "unknown account"})


@pytest.mark.parametrize("path", ["/api/reconcile", "/api/sweep"])
def test_acting_on_an_account_there_is_no_such_of(app, path):
    r = app.post(path, {"account": "nobody@example.test", "dry_run": True})
    assert (r.status, r.json) == (400, {"ok": False, "error": "unknown account"})


@pytest.mark.parametrize("field, said", [("port", "port must be 1–65535"), ("smtp_port", "SMTP port must be 1–65535")])
@pytest.mark.parametrize("junk", ["x", 10 ** 30])
def test_a_port_that_cant_be_a_number(app, field, said, junk):
    added = app.post("/api/accounts/add", {"email": "new@example.test", "type": "imap", "host": "mail.example.test",
                                           "credential": {"style": "plain", "value": "p"}, field: junk})
    assert (added.status, added.json) == (400, {"ok": False, "error": said})
    edited = app.post("/api/accounts/edit", {"email": w.ADA, field: junk})
    assert (edited.status, edited.json) == (400, {"ok": False, "error": said})


@pytest.mark.parametrize("junk", [[1], "x", 5, True])
def test_a_connection_test_of_what_cant_be_a_draft(app, junk):
    r = app.post("/api/accounts/test", {"draft": junk})
    assert (r.status, r.json) == (400, BAD_REQUEST)


WALK_LEAVES_OUT = {"/api/refresh", "/api/undo", "/api/accounts/delete"}     # each would undo the walk's own world
WALK_FIELDS = ("pk", "pks", "id", "draft_id", "account", "email", "thread_id", "from_addr", "token", "decision", "mode", "subject",
               "text", "undo_group", "idempotency_key", "in_reply_to_pk", "port", "draft", "rows", "to", "attachments", "resume")
WALK_JUNK = ([1], 10 ** 30, float("inf"))


def test_no_route_answers_junk_with_the_500_that_means_a_bug_here(app, world, ai):
    """Every POST route, each field a route has choked on, each kind of junk that got past the handler's own catch, within
    a body otherwise good enough to get as far as reading it."""
    pk = world.pk("ada-inbox-05-receipt.eml")
    good = {"pk": pk, "pks": [pk], "account": w.ADA, "email": w.ADA, "folder": "INBOX", "dest": "Archive", "seen": True, "id": 1,
            "thread_id": "t", "to": ["lin@example.test"], "subject": "Hi", "text": "Hello", "mode": "new", "dry_run": True,
            "token": "lin@example.test", "verdict": "keep", "preset": "tomorrow", "from_addr": "lin@example.test",
            "action": "keep", "verb": "archive", "decision": "accepted", "rows": [{"pk": pk}]}
    routes = sorted(path for method, path in web.H.routes._exact if method == "POST" and path not in WALK_LEAVES_OUT)
    routes += ["/api/accounts/test", "/api/accounts/trust-cert", "/api/accounts/add", "/api/accounts/edit"]
    bad = [(path, field, junk) for path in routes for field in WALK_FIELDS for junk in WALK_JUNK
           if app.post(path, {**good, field: junk}).status >= 500]
    app.settle(120)
    assert not bad
