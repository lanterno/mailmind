"""A request that doesn't read as one gets the 400 a malformed request gets, not the 500 that means a bug here."""
import pytest

import problems
import route_tables
from fixtures import world as w
from mailmind.bootstrap.mailbox import store
from mailmind.kernel.errors import Refused
from mailmind.kernel.parsed import as_int, required_int

BAD_REQUEST = {"type": "/problems/refused", "title": "Refused", "status": 400, "detail": "bad request"}
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


@pytest.mark.parametrize("path, name", [("/api/audit", "limit"), ("/api/debug/ai", "days"),
                                        ("/api/messages?account=" + w.ADA + "&folder=INBOX", "limit"),
                                        ("/api/messages?account=" + w.ADA + "&folder=INBOX", "offset"),
                                        ("/api/inboxes", "limit"), ("/api/inboxes", "offset"), ("/api/outbox", "limit"),
                                        ("/api/search?q=lunch", "limit"), ("/api/search?q=lunch", "offset")])
@pytest.mark.parametrize("junk", ["x", TOO_LARGE])
def test_a_number_in_a_query_string_that_wont_read(app, path, name, junk):
    r = app.get(f"{path}{'&' if '?' in path else '?'}{name}={junk}")
    assert (r.status, r.json) == (400, BAD_REQUEST)


def test_a_search_with_nothing_typed_answers_whatever_the_limit_says(app):
    r = app.get("/api/search?q=&limit=x")
    assert (r.status, r.json["ok"]) == (200, True)


@pytest.mark.parametrize("path", ["/api/message/body?key={}&folder=INBOX", "/api/message/attachment?key={}&folder=INBOX&part_id=1"])
@pytest.mark.parametrize("junk", ["x", TOO_LARGE, "1.5", "-"])
def test_a_message_named_by_what_cant_be_a_number(app, path, junk):
    r = app.get(path.format(junk))
    assert (r.status, r.json) == (400, BAD_REQUEST)


@pytest.mark.parametrize("path", ["/api/message/body?folder=INBOX", "/api/message/body?key=1",
                                  "/api/message/attachment?folder=INBOX&part_id=1", "/api/message/attachment?key=1&part_id=1",
                                  "/api/message/attachment?key=1&folder=INBOX"])
def test_a_copy_named_by_less_than_its_key_and_folder(app, path):
    r = app.get(path)
    assert (r.status, r.json) == (400, BAD_REQUEST)


@pytest.mark.parametrize("junk", ["x", TOO_LARGE])
def test_drafting_ahead_for_what_cant_be_a_message(app, junk):
    r = app.get(f"/api/ai/draft-ahead?key={junk}")
    assert (r.status, r.json) == (400, BAD_REQUEST)


def test_drafting_ahead_for_no_message_at_all(app):
    r = app.get("/api/ai/draft-ahead")
    assert (r.status, r.json) == (400, BAD_REQUEST)


@pytest.mark.parametrize("path", ["/api/outbox/retry", "/api/outbox/cancel", "/api/outbox/sendnow", "/api/pending/retry",
                                  "/api/pending/discard"])
@pytest.mark.parametrize("junk", ["x", 10 ** 30])
def test_a_queued_item_named_by_what_cant_be_a_number(app, path, junk):
    r = app.post(path, {"id": junk})
    assert (r.status, r.json) == (400, BAD_REQUEST)


@pytest.mark.parametrize("path, field", [("/api/trash", "key"), ("/api/ai/summarize", "key"), ("/api/ai/draft-reply", "key"),
                                         ("/api/mute", "key"), ("/api/unmute", "key"), ("/api/followups/dismiss", "key"),
                                         ("/api/replylater", "key"), ("/api/snooze", "key"), ("/api/unsnooze", "key"),
                                         ("/api/invite/rsvp", "key"), ("/api/rules", "key"), ("/api/draft/resume", "draft_id"),
                                         ("/api/draft/resume", "key")])
@pytest.mark.parametrize("junk", [[1], 10 ** 30, "x"])
def test_a_message_named_in_a_body_by_what_cant_be_a_number(app, path, field, junk):
    r = app.post(path, {field: junk, "folder": "INBOX", "preset": "tomorrow", "decision": "accepted", "token": "lin@example.test",
                        "verdict": "keep"})
    assert (r.status, r.json) == (400, BAD_REQUEST)


@pytest.mark.parametrize("path", ["/api/trash", "/api/ai/summarize", "/api/mute", "/api/snooze", "/api/invite/rsvp",
                                  "/api/rules", "/api/draft/resume"])
@pytest.mark.parametrize("junk", [[1], 5])
def test_a_copy_named_in_a_body_by_a_folder_that_isnt_text(app, world, path, junk):
    r = app.post(path, {"key": world.key("ada-inbox-02-lunch-confirm.eml"), "folder": junk, "preset": "tomorrow",
                        "decision": "accepted", "token": "lin@example.test", "verdict": "keep"})
    assert (r.status, r.json) == (400, BAD_REQUEST)


@pytest.mark.parametrize("path", ["/api/seen", "/api/flag", "/api/archive", "/api/junk", "/api/thread/archive",
                                  "/api/thread/trash"])
@pytest.mark.parametrize("junk", ["x", 10 ** 30])
def test_filing_what_cant_be_a_message(app, path, junk):
    r = app.post(path, {"key": junk, "folder": "Archive"})
    assert (r.status, r.json) == (400, BAD_REQUEST)


@pytest.mark.parametrize("junk", [[1], 10 ** 30])
def test_any_of_many_named_by_what_cant_be_a_number(app, world, junk):
    copy = world.copy("ada-inbox-02-lunch-confirm.eml")
    for path in ("/api/snooze", "/api/unsnooze"):
        r = app.post(path, {"copies": [copy, {"key": junk, "folder": "INBOX"}], "preset": "tomorrow"})
        assert (r.status, r.json) == (400, BAD_REQUEST)
    r = app.post("/api/bulk/file", {"verb": "archive", "rows": [copy, {"key": junk, "folder": "INBOX"}]})
    assert (r.status, r.json) == (400, BAD_REQUEST)
    c = store.conn()
    try:
        assert c.execute("SELECT COUNT(*) FROM snoozes").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM mail_commands").fetchone()[0] == 0
    finally:
        c.close()


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
    r = app.post("/api/send", {"account": w.ADA, "mode": "reply", "original_key": junk, "to": ["lin@example.test"], "text": "Hello"})
    assert (r.status, r.json) == (400, BAD_REQUEST)


@pytest.mark.parametrize("path, field", [("/api/ai/ask", "account"), ("/api/ai/draft-reply", "account"), ("/api/ai/summarize", "account"),
                                         ("/api/briefing/since", "account"), ("/api/draft", "account"),
                                         ("/api/send", "account"), ("/api/reconcile", "account"), ("/api/sweep", "account"),
                                         ("/api/snooze", "account"), ("/api/unsnooze", "account"), ("/api/sync-retry-account", "account"),
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
    r = app.post("/api/mute", {"account": None, "thread_id": None, **world.copy("ada-inbox-02-lunch-confirm.eml")})
    assert (r.status, r.json["ok"]) == (200, True)


@pytest.mark.parametrize("path", ["/api/draft", "/api/send"])
@pytest.mark.parametrize("junk", [[1], {"a": 1}, 5])
def test_a_file_named_by_anything_but_text(app, path, junk):
    r = app.post(path, {"account": w.ADA, "mode": "new", "to": ["lin@example.test"], "subject": "Hi", "text": "Hello",
                        "attachments": [{"filename": junk, "b64": "aGk="}]})
    assert (r.status, r.json) == (400, problems.refused("bad attachment"))


@pytest.mark.parametrize("verb, field", [("delete", "id"), ("create", "name"), ("create", "q")])
@pytest.mark.parametrize("junk", [[], [1], {"a": 1}, 5])
def test_a_saved_view_named_by_anything_but_text(app, verb, field, junk):
    before = app.get("/api/views").json
    r = app.post("/api/views", {"verb": verb, "name": "Lunch", "q": "lunch", field: junk})
    assert (r.status, r.json) == (400, BAD_REQUEST)
    assert app.get("/api/views").json == before


def test_asking_after_an_account_there_is_no_such_of(app):
    r = app.get("/api/folders?account=nobody@example.test")
    assert (r.status, r.json) == (400, problems.refused("unknown account"))


@pytest.mark.parametrize("path", ["/api/reconcile", "/api/sweep"])
def test_acting_on_an_account_there_is_no_such_of(app, path):
    r = app.post(path, {"account": "nobody@example.test", "dry_run": True})
    assert (r.status, r.json) == (400, problems.refused("unknown account"))


@pytest.mark.parametrize("field, said", [("port", "port must be 1–65535"), ("smtp_port", "SMTP port must be 1–65535")])
@pytest.mark.parametrize("junk", ["x", 10 ** 30])
def test_a_port_that_cant_be_a_number(app, field, said, junk):
    added = app.post("/api/accounts/add", {"email": "new@example.test", "type": "imap", "host": "mail.example.test",
                                           "credential": {"style": "keychain", "value": "p"}, field: junk})
    assert (added.status, added.json) == (400, problems.refused(said))
    edited = app.post("/api/accounts/edit", {"email": w.ADA, field: junk})
    assert (edited.status, edited.json) == (400, problems.refused(said))


@pytest.mark.parametrize("junk", [[1], "x", 5, True])
def test_a_connection_test_of_what_cant_be_a_draft(app, junk):
    r = app.post("/api/accounts/test", {"draft": junk})
    assert (r.status, r.json) == (400, BAD_REQUEST)


WALK_LEAVES_OUT = {"/api/refresh", "/api/undo", "/api/accounts/delete"}     # each would undo the walk's own world
WALK_FIELDS = ("key", "folder", "copies", "id", "draft_id", "account", "email", "thread_id", "from_addr", "token", "decision", "mode",
               "subject", "text", "undo_group", "idempotency_key", "original_key", "port", "draft", "rows", "to", "attachments",
               "resume")
WALK_JUNK = ([1], 10 ** 30, float("inf"))


def test_no_route_answers_junk_with_the_500_that_means_a_bug_here(app, world, ai):
    """Every POST route, each field a route has choked on, each kind of junk that once got past the catch around a route, within
    a body otherwise good enough to get as far as reading it."""
    copy = world.copy("ada-inbox-05-receipt.eml")
    good = {**copy, "copies": [copy], "account": w.ADA, "email": w.ADA, "seen": True, "id": 1,
            "thread_id": "t", "to": ["lin@example.test"], "subject": "Hi", "text": "Hello", "mode": "new", "dry_run": True,
            "token": "lin@example.test", "verdict": "keep", "preset": "tomorrow", "from_addr": "lin@example.test",
            "action": "keep", "verb": "archive", "decision": "accepted", "rows": [copy]}
    routes = sorted(path for method, path in route_tables.paths() if method == "POST" and path not in WALK_LEAVES_OUT)
    bad = [(path, field, junk) for path in routes for field in WALK_FIELDS for junk in WALK_JUNK
           if app.post(path, {**good, field: junk}).status >= 500]
    app.settle(120)
    assert not bad
