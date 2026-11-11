"""What a use case raises when it can't do what was asked, the status it already means, and the problem document (RFC 9457) the app
answers it with: its kind as the type and what a person is told as the detail, through the app's own routes. Anything else raised
is still the fixed 500, with only its class in the log. What else a problem says beside the four, one that says nothing, and that
a problem logs nothing are tests/unit/http/test_problems.py and test_route.py."""
import json
import logging

import pytest

import schema_contract
from mailmind.kernel.errors import Busy, Conflict, Locked, NotFound, Problem, Refused, Unreachable, Unreadable
from mailmind.platform.logging import get_logger
from problems import CONTENT_TYPE

SAID = "Words for a person."
STATUS = {NotFound: 404, Conflict: 409, Locked: 423, Refused: 400, Unreachable: 502, Unreadable: 422, Busy: 409}
KIND = {NotFound: ("not-found", "Not found"), Conflict: ("conflict", "Conflict"), Locked: ("locked", "Locked"),
        Refused: ("refused", "Refused"), Unreachable: ("unreachable", "Unreachable"), Unreadable: ("unreadable", "Unreadable"),
        Busy: ("busy", "Busy")}


ASKED = {"GET": "/api/stats", "POST": "/api/undo"}


def raising(monkeypatch, raised):
    """The one route these ask of each method, raising what it's given. What it answers then isn't what it declares."""
    def boom(*args, **kwargs):
        raise raised
    monkeypatch.setattr(schema_contract, "HOLDING", False)
    for method, path in ASKED.items():
        monkeypatch.setattr(schema_contract.route_for(method, path), "work", boom)


def asked(app, how):
    return app.get(ASKED["GET"]) if how == "GET" else app.post(ASKED["POST"], {})


@pytest.mark.parametrize("how", ["GET", "POST"])
@pytest.mark.parametrize("kind", STATUS, ids=lambda kind: kind.__name__)
def test_each_answers_the_status_it_means_as_a_problem_of_its_kind_with_what_a_person_is_told(app, monkeypatch, kind, how):
    raising(monkeypatch, kind(SAID))
    r = asked(app, how)
    name, title = KIND[kind]
    assert r.status == STATUS[kind] and r.headers["content-type"] == CONTENT_TYPE
    assert {k: r.json[k] for k in ("type", "title", "status", "detail")} == {"type": f"/problems/{name}", "title": title,
                                                                           "status": STATUS[kind], "detail": SAID}


@pytest.mark.parametrize("how", ["GET", "POST"])
@pytest.mark.parametrize("raised", [RuntimeError("hunter2"), KeyError("hunter2"), Problem("hunter2")], ids=repr)
def test_anything_else_is_the_fixed_500_and_its_class_in_the_log(app, monkeypatch, caplog, raised, how):
    raising(monkeypatch, raised)
    logger = logging.getLogger(get_logger("http").name)
    monkeypatch.setattr(logger, "propagate", True)
    with caplog.at_level(logging.ERROR, logger=logger.name):
        r = asked(app, how)
    assert (r.status, r.json) == (500, {"type": "about:blank", "title": "Internal Server Error", "status": 500, "detail": "request failed"})
    assert r.headers["content-type"] == CONTENT_TYPE
    assert "hunter2" not in json.dumps(r.json)
    assert [rec.exc_info[0] for rec in caplog.records] == [type(raised)]
