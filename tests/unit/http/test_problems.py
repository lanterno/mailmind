"""What a use case raises when it can't do what was asked, the status it already means, and the
{ok: false, error} the app reads. Anything else raised is still the fixed 500, with only its class in the log."""
import json
import logging

import pytest

from mailmind.bootstrap import web
from mailmind.http import handler
from mailmind.kernel import errors
from mailmind.kernel.errors import Busy, Conflict, Locked, NotFound, Problem, Refused, Unreachable, Unreadable
from mailmind.platform.logging import get_logger

SAID = "Words for a person."
STATUS = {NotFound: 404, Conflict: 409, Locked: 423, Refused: 400, Unreachable: 502, Unreadable: 422, Busy: 409}


class Raising:
    """The route table with one route, which raises what it's given."""

    def __init__(self, raised):
        self.raised = raised

    def find(self, method, path):
        def boom(req):
            raise self.raised
        return boom if path == "/api/raises" else None


def asked(app, how):
    return app.get("/api/raises") if how == "GET" else app.post("/api/raises", {})


@pytest.mark.parametrize("how", ["GET", "POST"])
@pytest.mark.parametrize("kind", STATUS, ids=lambda kind: kind.__name__)
def test_each_answers_the_status_it_means_with_what_a_person_is_told(app, monkeypatch, kind, how):
    monkeypatch.setattr(web.H, "routes", Raising(kind(SAID)))
    r = asked(app, how)
    assert r.status == STATUS[kind]
    assert r.json["ok"] is False and r.json["error"] == SAID


def test_every_one_has_a_status():
    assert set(Problem.__subclasses__()) == set(STATUS)
    assert handler._STATUS == STATUS


@pytest.mark.parametrize("how", ["GET", "POST"])
def test_what_else_it_says_goes_along(app, monkeypatch, how):
    monkeypatch.setattr(web.H, "routes", Raising(Conflict(SAID, conflict=True, version=7)))
    assert asked(app, how).json == {"ok": False, "error": SAID, "conflict": True, "version": 7}


def test_one_that_says_nothing_answers_with_no_error_at_all(app, monkeypatch):
    monkeypatch.setattr(web.H, "routes", Raising(NotFound()))
    r = app.post("/api/raises", {})
    assert (r.status, r.json) == (404, {"ok": False})


def test_busy_says_so_with_nothing_else(app, monkeypatch):
    monkeypatch.setattr(web.H, "routes", Raising(Busy()))
    r = app.post("/api/raises", {})
    assert (r.status, r.json) == (409, {"ok": False, "busy": True})


def test_a_malformed_request_is_refused_in_the_words_it_gets_today(app, monkeypatch):
    monkeypatch.setattr(web.H, "routes", Raising(Refused()))
    r = app.get("/api/raises")
    assert (r.status, r.json) == (400, {"ok": False, "error": "bad request"})


def test_a_refusal_is_no_trouble_of_the_apps_so_it_logs_nothing(app, monkeypatch, caplog):
    monkeypatch.setattr(web.H, "routes", Raising(Unreachable(SAID)))
    logger = logging.getLogger(get_logger("http").name)
    monkeypatch.setattr(logger, "propagate", True)
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        app.post("/api/raises", {})
    assert caplog.records == []


@pytest.mark.parametrize("how", ["GET", "POST"])
@pytest.mark.parametrize("raised", [RuntimeError("hunter2"), KeyError("hunter2"), Problem("hunter2")], ids=repr)
def test_anything_else_is_the_fixed_500_and_its_class_in_the_log(app, monkeypatch, caplog, raised, how):
    monkeypatch.setattr(web.H, "routes", Raising(raised))
    logger = logging.getLogger(get_logger("http").name)
    monkeypatch.setattr(logger, "propagate", True)
    with caplog.at_level(logging.ERROR, logger=logger.name):
        r = asked(app, how)
    assert (r.status, r.json) == (500, {"ok": False, "error": "request failed"})
    assert "hunter2" not in json.dumps(r.json)
    assert [rec.exc_info[0] for rec in caplog.records] == [type(raised)]


def test_the_kernel_knows_nothing_of_http():
    with open(errors.__file__) as f:
        source = f.read()
    assert "import http" not in source and not any(str(code) in source for code in STATUS.values())
