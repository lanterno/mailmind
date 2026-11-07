"""The route table: which function answers which path.

There is no framework here: `python3 server.py`, the old front door, serves this same table with nothing installed.
A route names its path as a literal string, so to find one, grep the path. A route takes a `Request` and returns what to
answer: an object (a 200), `(object, code)`, or `ANSWERED` when it wrote the answer itself.

Standard library only: python3 server.py uses it.
"""
from dataclasses import dataclass

from mailmind.kernel.parsed import required_int

ANSWERED = object()


@dataclass(frozen=True)
class Request:
    handler: object             # the http.server handler, for a route that writes its own answer
    path: str
    query: dict                 # as parse_qs gives it, a list for each name
    body: dict | None = None    # a POST's JSON object

    def arg(self, name, default=None):
        """The first value the query string gives `name`."""
        return self.query.get(name, [default])[0]

    def int_arg(self, name, default):
        """The number the query string gives `name`, or `default` with none. Refused if it won't read as one."""
        given = self.arg(name)
        return default if given is None else required_int(given)


class Routes:
    def __init__(self, *others):
        self._exact = {}
        self._matching = []
        for other in others:
            self._exact.update(other._exact)
            self._matching.extend(other._matching)

    def _on(self, method, path):
        def add(fn):
            assert (method, path) not in self._exact, path
            self._exact[method, path] = fn
            return fn
        return add

    def get(self, path):
        return self._on("GET", path)

    def post(self, path):
        return self._on("POST", path)

    def matching(self, method, matches):
        """A route for the paths `matches(path)` says yes to, asked only when no literal path answers."""
        def add(fn):
            self._matching.append((method, matches, fn))
            return fn
        return add

    def find(self, method, path):
        """The route for a request, or None."""
        fn = self._exact.get((method, path))
        if fn is not None:
            return fn
        for m, matches, route in self._matching:
            if m == method and matches(path):
                return route
        return None
