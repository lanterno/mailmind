"""The words every schema is written in.

A request's model is a gate: it names every field its use case reads, typed, and a value it refuses is the `bad request` 400 (the
route class says so). A use case takes a dict, so a handler passes `body.model_dump(exclude_unset=True)`, which leaves out what the
request didn't say. A field is optional unless going without one already answers `bad request`, since what a request leaves out is
the use case's to word. A field nobody declares goes nowhere: under test, a use case that reads one is a failure.

An answer's model is never a gate, and a handler passes no `response_model`, which would filter and refuse. `answers` declares
it for OpenAPI, and only under test is each JSON answer held to it (tests/schema_contract.py), which is what says the models are
true. A route's answer that varies by branch is a union of models, `ok: false` with a 200 included.
"""
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, StrictBool

from mailmind.kernel.parsed import as_int


def _number(given):
    number = as_int(given)
    if number is None:
        raise ValueError("not a number")
    return number


# A number as a request may give it (kernel.parsed): "7" and 7.0 read, and nothing past 2^53 does. Lax, pydantic would refuse 7.5,
# which reads as 7, and take a number past what a row holds.
Number = Annotated[int, BeforeValidator(_number)]

# True or false. Lax, pydantic would read "no" as false, where a use case's own `if` reads it as true: better refused than turned.
Flag = StrictBool


class RequestBody(BaseModel):
    """A POST's JSON object. What it doesn't name is ignored, as it always was. Text is `str | None`: pydantic takes no number
    for a string, even in its lax mode."""
    model_config = ConfigDict(extra="ignore")


class NoBody(RequestBody):
    """A POST that reads nothing of its body, which must still be a JSON object."""


class QueryArgs(BaseModel):
    """A GET's query string, as `Annotated[Model, Query()]`. `Route` hands it to FastAPI as `parse_qs` reads it, each name's first
    value, and a blank one is none, so an optional field takes its default."""
    model_config = ConfigDict(extra="ignore")

    def as_query(self):
        """For a use case that takes the query string whole, as `parse_qs` gives it: a list for each name."""
        return {name: [str(value)] for name, value in self.model_dump(exclude_unset=True).items()}


class Answer(BaseModel):
    """What a route answers. It holds every field a route's answer may have, so a new one is a failure under test."""
    model_config = ConfigDict(extra="forbid")


class NotJson(Answer):
    """A file, a document or a stream, which is what its route says it is. Nothing holds an answer that isn't JSON to a model."""


class Failure(Answer):
    """What any route may answer when it can't (`shared.failure_answer`), or the perimeter when it won't: a problem document
    (RFC 9457), as application/problem+json. `type` names the kind of problem, `/problems/<kind>` or `about:blank`, and `detail`
    is what a person is told, left out where the page words it. The rest is what the page acts on. A route that says more (a
    Problem's `more`) names a subclass of its own for that status, through `answers`."""
    type: str
    title: str
    status: int
    detail: str | None = None
    busy: Literal[True] | None = None
    read_only: Literal[True] | None = None
    ai_off: Literal[True] | None = None
    over_limit: Literal[True] | None = None
    spent_micros: int | None = None
    limit_micros: int | None = None


_FAILS = (400, 402, 403, 404, 409, 413, 422, 423, 500, 502)


def answers(ok, more=None) -> dict[int | str, dict[str, Any]]:
    """`responses=` for a route: `ok` at 200 and `Failure` at each status a failure answers, with `more` as {status: model} for
    the rest, or where a route says more than `Failure` does."""
    return {200: {"model": ok}, **{code: {"model": Failure} for code in _FAILS},
            **{code: {"model": model} for code, model in (more or {}).items()}}
