# Routing

Every route is FastAPI's: `mailmind/http/routers/<context>.py` gives `router(context)`, which `mailmind.bootstrap.web.ROUTERS` hands
the app, and `mailmind/http/schemas/<context>.py` its models. What a failure answers, who counts as someone here and how far a body
may run are in `mailmind.http.shared`. What no route matches is `_no_route` in `http/app.py`, the app's `router.default`, after the
perimeter like the rest: `not found` as a 404 for a GET or a POST, and `not implemented` as a 501 for any other method, reading no
body. An absolute-form request line is no route either, since the path goes as sent.

A route, as `routers/attention.py` writes one:

```python
r = route.router()      # mailmind.http.route

@r.post("/api/snooze", responses=answers(Snoozed))
def snooze(body: SnoozeBody):
    return attention.snoozing.snooze(body.model_dump(exclude_unset=True))
```

- A handler is a plain `def` naming its **literal path**, a parameter only where the path has one (`/m/{place:path}`, after
  the literal paths it could shadow). `Route` refuses an `async def`, a `response_model` and a POST with no body model, `NoBody` if
  it reads nothing, as that model is what refuses a body that isn't a JSON object. It returns an object, `(object, code)` or a
  `Response` for what isn't JSON, naming `Content-Type` in `headers=`, since `media_type` adds a charset.
- `Route` does what every route needs, so a handler holds none of it: `for_request`, `allowing_over`, `shared.touched`, the
  error mapping below, and `txguard.release_thread`, since a pooled thread outlives its request. It hands FastAPI a query string's
  first value for each name with a blank as none (`route.query_as_sent` keeps it as the client wrote it, for what repeats it), a body
  as JSON whatever its Content-Type, and {} for one with no declared length. It writes an answer in `json.dumps`' bytes, on the
  thread. A method the route doesn't take goes to `_no_route`.
- A `Response` runs after the handler has returned, on the event loop, past what a failure answers. So a handler does what can fail
  first, and an answer that streams is a `StreamingResponse` with a `__call__` of its own around `super().__call__`, for what must be
  given back however it ends (`routers/events.py`, `routers/export.py`): a generator a client left at its `yield` runs its `finally`
  only when it is collected.
- A request's model (`schemas.common.RequestBody`, or `QueryArgs` as `Annotated[Model, Query()]`, whose `as_query()` is for a use
  case that takes the query whole) names every field its use case reads, typed in `common`'s words: `str | None`, `Number`
  (`kernel.parsed`'s reading, nothing past 2^53) and `Flag`. It stays optional unless going without already answers `bad request`,
  so a use case keeps its own words for what is left out. What the model refuses answers the `bad request` problem
  (`/problems/refused`) with 400, GET included.
- A field that names one of a closed set (a snooze preset, a tone, a rewrite, a verb, a verdict, a decision, a mode) is typed as the
  set, a `type Name = Literal[...]` declared once in the domain beside what its use case checks against (a tuple of `get_args`, or a
  table keyed by it, which `tests/unit/http/test_closed_sets.py` holds to every member), and reached through the context's `api`: a
  set two contexts name is the kernel's (`kernel.snoozed.SnoozePreset`). Pydantic names it in the OpenAPI document, so the page has it
  as a type. A value outside it is the `bad request`, and one left out is still the use case's to word. An answer that names one says
  so too (`PlanPreview.verb`). Accounts' `type` and credential stay `Any`, since Accounts words its own refusals of them.
- An answer's model (`schemas.common.Answer`) is documentation: `responses=answers(Ok, {409: MoreSaid})`, never `response_model`,
  which would filter and refuse. `Ok` may be a union of what a route answers when it can, and is never a refusal. `answers` gives
  each failure status `Failure`, the problem document, which `tests/openapi_doc.py` gives as `application/problem+json`; a route
  whose `Problem` says more names a subclass of `Failure`.
- Under test (`tests/schema_contract.py`, through the `app` fixture) each JSON answer of a route must fit its model,
  with no field it doesn't name, and a use case reading a name its model doesn't declare fails the test. So the suite is what says
  a model is true, and a golden that changes is a question, not an update. The page's types come from the models
  (`just gen-api`), so a model that says less than its route answers is one the page can't read. A file, a document or a stream
  answers `answers(NotJson)`: `Route` gives its 200 no model and no media type, so OpenAPI gives it no JSON. What walks the routes (`tests/route_tables.py`: the perimeter matrix, the error scrubbing, the
  malformed-input walk) reads the routers, so a new route needs no edit there, and a stand-in for a handler is `route.work`.

A route or a use case that can't do what was asked raises one of `mailmind.kernel.errors`, carrying what a person is told, and
`shared.failure_answer` answers it as a problem document (RFC 9457), `application/problem+json`, by `shared.KINDS`: `NotFound` 404,
`Conflict` and `Busy` 409, `Locked` 423, `Refused` 400, `Unreachable` 502 (a retry can help), `Unreadable` 422. Its `type` is
`/problems/<kind>` (`not-found`, `conflict`, `busy`, ...), its `title` the kind's, its `status` the answer's, and its `detail` what the
person is told, left out where the page words it. What else it says (`Problem.more`: `busy`, `conflict`, `empty`, `retry` and the
like) goes beside those four and never stands in for one. `gates.Busy` is the kernel's. A use case returns only the answer it has, a
dict, and a route with a catch of its own lets `Problem` through ahead of it (`except Problem: raise`). Unexpected exceptions still
answer the fixed 500, `about:blank` and `request failed`, with the class logged. `AIOff` (`/problems/ai-off`, 409), `OverLimit`
(`/problems/over-limit`, 402), `ReadOnlyError` (`/problems/read-only`, 409) and `TxAcrossNetworkError` keep the paths the sections
below give them. On a POST, any `TypeError` or `ValueError` is still the 400. On a GET it isn't.

No refusal answers a 200: `ok` is only ever `true`, on what a route did. A result stays a 200, though it may say not all went: a
login that didn't work (`connect: false`), a group Undo that reversed some (`failed`), a move the worker has (`queued`, `waiting`),
marked rows some of which stayed (`rows_failed`) and a prefetch that gave way (`prefetched: false`). What means none of the kinds is
`about:blank`, titled by its status, through `route.problem(status, detail, **more)`: 410 for a plan expired, 413, 503 for too many
listeners, 504 for a summary still going, and each route's own worded 500. The perimeter's 403s and 413s answer the same
(`shared.problem`), and so does a path of the API no route takes (`app._no_route`: 404, or 501 for a method the app has none
of). What isn't the API answers as it did, in text: the page's 404 when nothing is built, an asset's 404 and `_no_route`'s 404
and 501 elsewhere. A mail body's frame (`body.html`) answers a failure as a document, as before. The page reads a failure as an
`ApiError` (`web/src/lib/api/client.ts`): its `detail`, `type`, flags and the `problem` itself, and `refusalOf` says whether it is
the server's no, a 4xx.

Consequence: to find a route, grep the literal path.
