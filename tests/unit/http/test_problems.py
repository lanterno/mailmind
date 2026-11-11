"""What a use case raises when it can't do what was asked, the status it already means, and the problem document (RFC 9457) the app
answers it with: its kind as the type, what a person is told as the detail, and what else it says beside them. Anything else raised
is still the fixed 500, with only its class in the log."""

import pytest

from mailmind.http import shared
from mailmind.kernel import errors
from mailmind.kernel.errors import Busy, Conflict, Locked, NotFound, Problem, Refused, Unreachable, Unreadable
STATUS = {NotFound: 404, Conflict: 409, Locked: 423, Refused: 400, Unreachable: 502, Unreadable: 422, Busy: 409}
KIND = {NotFound: ("not-found", "Not found"), Conflict: ("conflict", "Conflict"), Locked: ("locked", "Locked"),
        Refused: ("refused", "Refused"), Unreachable: ("unreachable", "Unreachable"), Unreadable: ("unreadable", "Unreadable"),
        Busy: ("busy", "Busy")}


def test_every_one_has_a_status_and_a_kind():
    assert set(Problem.__subclasses__()) == set(STATUS) == set(shared.KINDS)
    assert shared.STATUS == STATUS
    assert {kind: name for kind, (_, name) in shared.KINDS.items()} == {kind: name for kind, (name, _) in KIND.items()}


@pytest.mark.parametrize("status, detail, more, doc", [
    (410, "this plan has expired", {"expired": True},
     {"type": "about:blank", "title": "Gone", "status": 410, "detail": "this plan has expired", "expired": True}),
    (413, "too large", {}, {"type": "about:blank", "title": "Content Too Large", "status": 413, "detail": "too large"}),
    (503, "", {}, {"type": "about:blank", "title": "Service Unavailable", "status": 503}),
])
def test_one_that_means_none_of_the_kinds_is_about_blank_titled_by_its_status(status, detail, more, doc):
    assert shared.problem(status, detail, **more) == (doc, status)


def test_the_kernel_knows_nothing_of_http():
    with open(errors.__file__) as f:
        source = f.read()
    assert "import http" not in source and not any(str(code) in source for code in STATUS.values())
