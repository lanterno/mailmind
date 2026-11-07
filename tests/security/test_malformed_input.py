"""A request that doesn't read as one gets the 400 a malformed request gets, not the 500 that means a bug here."""
import pytest

from fixtures import world as w
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
@pytest.mark.parametrize("junk", ["x", "1.5", TOO_LARGE])
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
@pytest.mark.parametrize("junk", ["x", [1], 10 ** 30, float("inf")])
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
@pytest.mark.parametrize("junk", [[1], 10 ** 30, float("inf")])
def test_a_message_named_in_a_body_by_what_cant_be_a_number(app, path, field, said, junk):
    r = app.post(path, {field: junk, "preset": "tomorrow"})
    assert (r.status, r.json) == (400, {"ok": False, "error": said})


@pytest.mark.parametrize("path", ["/api/seen", "/api/flag", "/api/archive", "/api/junk", "/api/move", "/api/thread/archive",
                                  "/api/thread/trash"])
@pytest.mark.parametrize("junk", ["x", [1], 10 ** 30, float("inf")])
def test_filing_what_cant_be_a_message_answers_as_any_refused_filing_does(app, path, junk):
    r = app.post(path, {"pk": junk, "folder": "Archive"})
    assert (r.status, r.json) == (200, {"ok": False, "msg": "bad pk"})


@pytest.mark.parametrize("junk", [[1], 10 ** 30, float("inf")])
def test_any_of_many_named_by_what_cant_be_a_number(app, world, junk):
    pk = world.pk("ada-inbox-02-lunch-confirm.eml")
    for path in ("/api/snooze", "/api/unsnooze"):
        r = app.post(path, {"pks": [pk, junk], "preset": "tomorrow"})
        assert (r.status, r.json) == (400, BAD_REQUEST)
    assert not world.row("ada-inbox-02-lunch-confirm.eml")["snoozed_until"]


@pytest.mark.parametrize("path", ["/api/draft", "/api/send"])
@pytest.mark.parametrize("junk", ["x", [1], 10 ** 30, float("inf")])
def test_a_save_or_a_send_naming_what_cant_be_a_draft(app, path, junk):
    r = app.post(path, {"draft_id": junk, "account": w.ADA, "mode": "new", "to": ["lin@example.test"], "subject": "Hi", "text": "Hello"})
    assert (r.status, r.json) == (400, BAD_REQUEST)
    assert not app.get("/api/outbox").json["items"]


@pytest.mark.parametrize("junk", ["x", [1], 10 ** 30, float("inf")])
def test_a_discard_naming_what_cant_be_a_draft(app, junk):
    r = app.post("/api/draft", {"draft_id": junk, "delete": True})
    assert (r.status, r.json) == (400, BAD_REQUEST)


@pytest.mark.parametrize("junk", ["x", [1], 10 ** 30, float("inf")])
def test_a_reply_naming_what_cant_be_its_original(app, junk):
    r = app.post("/api/send", {"account": w.ADA, "mode": "reply", "in_reply_to_pk": junk, "to": ["lin@example.test"], "text": "Hello"})
    assert (r.status, r.json) == (400, {"ok": False, "error": "missing original message"})
