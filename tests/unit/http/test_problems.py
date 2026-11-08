"""What a use case raises when it can't do what was asked, the status it already means, and the problem document (RFC 9457) the app
answers it with: its kind as the type, what a person is told as the detail, and what else it says beside them. Anything else raised
is still the fixed 500, with only its class in the log."""
import json
import logging

import pytest

import schema_contract
from mailmind.http import shared
from mailmind.kernel import errors
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


def test_every_one_has_a_status_and_a_kind():
    assert set(Problem.__subclasses__()) == set(STATUS) == set(shared.KINDS)
    assert shared.STATUS == STATUS
    assert {kind: name for kind, (_, name) in shared.KINDS.items()} == {kind: name for kind, (name, _) in KIND.items()}


@pytest.mark.parametrize("how", ["GET", "POST"])
def test_what_else_it_says_goes_along_beside_the_four(app, monkeypatch, how):
    raising(monkeypatch, Conflict(SAID, conflict=True, version=7))
    assert asked(app, how).json == {"type": "/problems/conflict", "title": "Conflict", "status": 409, "detail": SAID,
                                    "conflict": True, "version": 7}


def test_what_else_it_says_never_stands_in_for_one_of_the_four(app, monkeypatch):
    raising(monkeypatch, NotFound(SAID, type="elsewhere", title="x", status=200, detail="other words", gone=True))
    assert asked(app, "POST").json == {"type": "/problems/not-found", "title": "Not found", "status": 404, "detail": SAID,
                                       "gone": True}


def test_one_that_says_nothing_answers_with_no_detail_at_all(app, monkeypatch):
    raising(monkeypatch, NotFound())
    r = asked(app, "POST")
    assert (r.status, r.json) == (404, {"type": "/problems/not-found", "title": "Not found", "status": 404})


def test_busy_says_so_with_nothing_else(app, monkeypatch):
    raising(monkeypatch, Busy())
    r = asked(app, "POST")
    assert (r.status, r.json) == (409, {"type": "/problems/busy", "title": "Busy", "status": 409, "busy": True})


def test_a_malformed_request_is_refused_in_the_words_it_gets_today(app, monkeypatch):
    raising(monkeypatch, Refused())
    r = asked(app, "GET")
    assert (r.status, r.json) == (400, {"type": "/problems/refused", "title": "Refused", "status": 400, "detail": "bad request"})


def test_a_refusal_is_no_trouble_of_the_apps_so_it_logs_nothing(app, monkeypatch, caplog):
    raising(monkeypatch, Unreachable(SAID))
    logger = logging.getLogger(get_logger("http").name)
    monkeypatch.setattr(logger, "propagate", True)
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        asked(app, "POST")
    assert caplog.records == []


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
