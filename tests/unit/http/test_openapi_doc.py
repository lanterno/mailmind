"""tests/openapi_doc.py, the OpenAPI document `just gen-api` turns into the page's types: every route in it, in the words the page
reads, and written with nothing read of the data directory and nothing sent.
"""
import json
import pathlib
import subprocess
import sys

import pytest

import openapi_doc
import route_tables

ROOT = pathlib.Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def doc():
    return openapi_doc.document()


def answered(doc, path, method="get", status="200"):
    return doc["paths"][path][method]["responses"][status]


def test_every_route_is_in_it(doc):
    said = {(method.upper(), path) for path, ops in doc["paths"].items() for method in ops}
    assert said == {(method, route.path_format) for route in route_tables.every_route() for method in route.methods}


def test_a_model_has_one_name_whether_it_is_asked_or_answered(doc):
    assert not [name for name in doc["components"]["schemas"] if name.endswith(("-Input", "-Output"))]


def test_a_number_and_a_flag_read_as_the_page_reads_them(doc):
    snooze = doc["components"]["schemas"]["SnoozeBody"]["properties"]
    assert {"type": "integer"} in snooze["pk"]["anyOf"] and {"type": "string"} in snooze["until"]["anyOf"]
    assert {"type": "boolean"} in doc["components"]["schemas"]["ReplyLaterBody"]["properties"]["on"]["anyOf"]


def test_an_answer_is_closed_and_what_it_may_leave_out_isnt_required(doc):
    filed = doc["components"]["schemas"]["Filed"]
    assert filed["additionalProperties"] is False
    assert set(filed["required"]) == {"ok", "msg", "undoable"} and "status" in filed["properties"]


def test_a_route_that_answers_no_json_has_no_json_type(doc):
    for path in ("/api/events", "/api/export", "/api/message/{pk}/body.html", "/api/message/{pk}/attachment/{part_id}", "/"):
        assert "content" not in answered(doc, path), path
    assert "application/json" in answered(doc, "/api/events", status="503")["content"]


def test_it_writes_the_document_as_a_program_reading_nothing_and_sending_nothing(tmp_path):
    out = tmp_path / "openapi.json"
    run = subprocess.run([sys.executable, str(ROOT / "tests" / "openapi_doc.py"), str(out)], cwd=tmp_path, capture_output=True, text=True,
                         timeout=120)
    assert run.returncode == 0, run.stderr
    assert json.loads(out.read_text(encoding="utf-8")) == json.loads(json.dumps(openapi_doc.document()))


def test_it_stops_at_any_try_to_connect():
    with pytest.raises(RuntimeError, match="socket.connect"):
        openapi_doc._refuse("socket.connect", ())
    openapi_doc._refuse("open", ())
