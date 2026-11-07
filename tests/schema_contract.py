"""Under test, the schemas are held to what the routes really do. At run time an answer's model is documentation and a body's only
says what gets through, so nothing else would notice them going wrong:

- Each JSON answer of a route must fit the model `responses` gives its status, with no field it doesn't name
  (`HoldingAnswers`, which the `app` fixture serves the app through).
- A use case that reads a name its request's model doesn't declare would get nothing, whatever the client sent, because the model
  dropped it (`watch_reads`).

What breaks either goes in `violations`, and the `app` fixture fails the test as it ends.
"""
import json

from pydantic import BaseModel, TypeAdapter, ValidationError

import route_tables
from mailmind.http.schemas.common import QueryArgs, RequestBody

HOLDING = True      # off for a test whose routes stand in for the real ones, and answer accordingly
violations = []
_adapters = {}


def route_for(method, path):
    """The route that answers this request, or None. `path` is as sent."""
    for route in route_tables.every_route():
        if method in route.methods and route.path_regex.match(path):
            return route
    return None


def fault_in(route, status, answer):
    """What's wrong with `answer`, JSON as it parsed, as what `route` answers with `status`, or None."""
    declared = route.responses.get(status)
    if declared is None:
        return f"{route.path} answered {status}, which it doesn't declare: {answer!r}"
    model = declared["model"]
    try:
        _adapters.setdefault(model, TypeAdapter(model)).validate_python(answer, strict=True)
    except ValidationError as e:
        return f"{route.path} answered {status} with what {model} doesn't say: {e}"
    return None


class HoldingAnswers:
    """An ASGI app that passes everything to `app`, and looks at what a route answered."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        route = route_for(scope["method"], scope["raw_path"].decode("latin-1")) if scope["type"] == "http" else None
        if route is None or not HOLDING:
            return await self.app(scope, receive, send)
        status, json_answer, chunks = None, False, []

        async def noting(message):
            nonlocal status, json_answer
            if message["type"] == "http.response.start":
                status = message["status"]
                json_answer = dict(message["headers"]).get(b"content-type", b"").startswith(b"application/json")
            elif json_answer:
                chunks.append(message.get("body", b""))
            await send(message)
        await self.app(scope, receive, noting)
        if json_answer:
            fault = fault_in(route, status, json.loads(b"".join(chunks)))
            if fault:
                violations.append(fault)


class _Reads(dict):
    """A request as a use case gets it, which notes a read of a name its model doesn't declare."""

    def __init__(self, given, model):
        super().__init__(given)
        self._model = model

    def _note(self, name):
        if name not in self._model.model_fields:
            violations.append(f"its use case read {name!r}, which {self._model.__name__} doesn't declare, so the client's goes nowhere")

    def get(self, name, default=None):
        self._note(name)
        return super().get(name, default)

    def __getitem__(self, name):
        self._note(name)
        return super().__getitem__(name)

    def __contains__(self, name):
        self._note(name)
        return super().__contains__(name)

    def pop(self, name, *default):
        self._note(name)
        return super().pop(name, *default)

    def setdefault(self, name, default=None):
        self._note(name)
        return super().setdefault(name, default)


def _model_dump(self, **kw):
    return _Reads(BaseModel.model_dump(self, **kw), type(self))


def watch_reads(monkeypatch):
    """What a handler hands a use case, from here on, notes what the use case reads."""
    plain = QueryArgs.as_query

    def as_query(self):
        return _Reads(plain(self), type(self))
    monkeypatch.setattr(RequestBody, "model_dump", _model_dump)
    monkeypatch.setattr(QueryArgs, "model_dump", _model_dump)
    monkeypatch.setattr(QueryArgs, "as_query", as_query)


def verify():
    """Fail the test that's ending if its schemas weren't true, and start the next afresh."""
    found = [*violations]
    violations.clear()
    assert not found, "\n".join(found)
