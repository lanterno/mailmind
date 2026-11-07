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
