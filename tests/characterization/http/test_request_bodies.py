"""A request body that isn't a JSON object: the bad request, and what the perimeter says first."""
import pytest

import problems


@pytest.mark.parametrize("body", [b"[1, 2]", b'"pk"', b"7", b"null", b"{not json"])
def test_a_json_body_that_isnt_an_object_is_a_bad_request(app, body):
    r = app.post("/api/seen", body)
    assert (r.status, r.json) == (400, problems.BAD_REQUEST)


def test_a_body_that_isnt_an_object_is_refused_by_the_guards_first(app):
    r = app.post("/api/seen", b"[1, 2]", headers={"X-CSRF-Token": None})
    assert r.status == 403
    assert app.post("/api/undo", b"").json == problems.not_found(empty=True)   # no body at all is still {}: nothing to undo
