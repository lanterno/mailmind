"""tests/schema_contract.py, which holds the schemas to what the routes do, and a walk of every route. A check that never fires says
nothing, so here each is made to.
"""
import pytest
from pydantic import BaseModel

import route_tables
import schema_contract
from mailmind.http.route import router
from mailmind.http.schemas.common import Answer, Failure, NoBody, QueryArgs, RequestBody, answers


class Said(Answer):
    ok: bool
    count: int


r = router()


@r.post("/api/said", responses=answers(Said))
def said(body: NoBody):
    return {"ok": True, "count": 1}


ROUTE = r.routes[0]


@pytest.mark.parametrize("status, answer", [(200, {"ok": True, "count": 1}), (500, {"ok": False, "error": "request failed"}), (409, {"ok": False, "busy": True}),
                                            (403, {"ok": False, "error": "bad or missing CSRF token"})])
def test_what_fits_says_nothing(status, answer):
    assert schema_contract.fault_in(ROUTE, status, answer) is None


@pytest.mark.parametrize("status, answer, says", [(200, {"ok": True}, "count"), (200, {"ok": True, "count": 1, "more": 2}, "more"), (200, {"ok": True, "count": "1"}, "count"),
                                                  (200, {"ok": True, "count": None}, "count"), (500, {"ok": False, "why": "x"}, "why"), (201, {"ok": True, "count": 1}, "doesn't declare")])
def test_what_doesnt_is_a_fault(status, answer, says):
    assert says in schema_contract.fault_in(ROUTE, status, answer)


def test_a_union_takes_either():
    class Other(Answer):
        ok: bool
        msg: str
    either = router()

    @either.post("/api/either", responses=answers(Said | Other))
    def what(body: NoBody):
        return {}
    route = either.routes[0]
    assert schema_contract.fault_in(route, 200, {"ok": False, "msg": "no"}) is None
    assert schema_contract.fault_in(route, 200, {"ok": True, "count": 1}) is None
    assert schema_contract.fault_in(route, 200, {"ok": True}) is not None


def test_each_failure_a_route_shares_has_a_model():
    """So a status the route class answers is never one a route hasn't declared."""
    assert {400, 402, 403, 404, 409, 413, 422, 423, 500, 502} <= set(ROUTE.responses)
    assert ROUTE.responses[409]["model"] is Failure


class Reads(RequestBody):
    declared: str | None = None


class Asks(QueryArgs):
    declared: str | None = None


@pytest.mark.parametrize("read", [lambda b: b.get("declared"), lambda b: "declared" in b, lambda b: b.get("declared", 1)])
def test_a_use_case_may_read_what_its_model_declares(monkeypatch, read):
    schema_contract.watch_reads(monkeypatch)
    read(Reads(declared="x").model_dump(exclude_unset=True))
    read(Asks(declared="x").as_query())
    schema_contract.verify()


@pytest.mark.parametrize("read", [lambda b: b.get("nobody"), lambda b: b["nobody"] if "nobody" in b else None, lambda b: b.pop("nobody", None),
                                  lambda b: b.setdefault("nobody", 1)])
@pytest.mark.parametrize("given", [lambda: Reads(declared="x").model_dump(exclude_unset=True), lambda: Asks(declared="x").as_query()])
def test_a_use_case_that_reads_what_its_model_doesnt_declare_is_caught(monkeypatch, read, given):
    schema_contract.watch_reads(monkeypatch)
    read(given())
    with pytest.raises(AssertionError, match="nobody"):
        schema_contract.verify()


def test_what_a_model_dump_holds_still_reads_as_it_should(monkeypatch):
    schema_contract.watch_reads(monkeypatch)
    dumped = Reads(declared="x").model_dump(exclude_unset=True)
    assert dumped == {"declared": "x"} and dumped["declared"] == "x" and isinstance(dumped, dict)
    assert Reads().model_dump(exclude_unset=True) == {}
    assert isinstance(BaseModel.model_dump(Reads()), dict)


# ── every route ──────────────────────────────────────────────────────────────────────────────────────

def body_model(route):
    return route.body_field.field_info.annotation if route.body_field else None


@pytest.mark.parametrize("route", route_tables.every_route(), ids=lambda route: f"{sorted(route.methods)[0]} {route.path}")
def test_a_route_says_what_it_takes_and_answers(route):
    """Every one in the same words, so the whole suite holds it to them."""
    def is_answer(model):
        members = getattr(model, "__args__", (model,))
        return all(issubclass(member, Answer) for member in members)
    assert 200 in route.responses and all(is_answer(said["model"]) for said in route.responses.values())
    if "POST" in route.methods:
        assert issubclass(body_model(route), RequestBody)
