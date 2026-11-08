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
    assert {"type": "integer"} in snooze["key"]["anyOf"] and {"type": "string"} in snooze["until"]["anyOf"]
    assert {"type": "boolean"} in doc["components"]["schemas"]["ReplyLaterBody"]["properties"]["on"]["anyOf"]


def test_an_answer_is_closed_and_what_it_may_leave_out_isnt_required(doc):
    filed = doc["components"]["schemas"]["Filed"]
    assert filed["additionalProperties"] is False
    assert set(filed["required"]) == {"ok", "msg", "undoable"} and "status" in filed["properties"]


def test_a_route_that_answers_no_json_has_no_json_type(doc):
    for path in ("/api/events", "/api/export", "/api/export/archive", "/api/message/body.html", "/api/message/attachment", "/"):
        assert "content" not in answered(doc, path), path
    assert list(answered(doc, "/api/events", status="503")["content"]) == ["application/problem+json"]


def test_every_failure_a_route_declares_is_a_problem_document_and_no_success_is(doc):
    said = {(status[:1], media) for ops in doc["paths"].values() for op in ops.values()
            for status, answer in op["responses"].items() for media in answer.get("content", {})}
    assert {media for kind, media in said if kind in "45"} == {"application/problem+json"}
    assert "application/problem+json" not in {media for kind, media in said if kind == "2"}
    assert answered(doc, "/api/undo", "post", "500")["content"]["application/problem+json"]["schema"]["$ref"].endswith("/Failure")


def test_it_writes_the_document_as_a_program_reading_nothing_and_sending_nothing(tmp_path):
    out = tmp_path / "openapi.json"
    run = subprocess.run([sys.executable, str(ROOT / "tests" / "openapi_doc.py"), str(out)], cwd=tmp_path, capture_output=True, text=True,
                         timeout=120)
    assert run.returncode == 0, run.stderr
    assert json.loads(out.read_text(encoding="utf-8")) == json.loads(json.dumps(openapi_doc.document()))


def test_given_a_second_path_it_writes_the_pages_limits_there_too(tmp_path):
    out, limits = tmp_path / "openapi.json", tmp_path / "limits.gen.ts"
    run = subprocess.run([sys.executable, str(ROOT / "tests" / "openapi_doc.py"), str(out), str(limits)], cwd=tmp_path,
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr
    assert limits.read_text(encoding="utf-8") == openapi_doc.limits_module()


def test_it_stops_at_any_try_to_connect():
    with pytest.raises(RuntimeError, match="socket.connect"):
        openapi_doc._refuse("socket.connect", ())
    openapi_doc._refuse("open", ())
