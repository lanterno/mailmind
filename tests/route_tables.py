"""Every route the app has. What walks the routes asks here, so a route added tomorrow is walked, with no edit to the test."""
import re

from mailmind.bootstrap import web
from mailmind.http.route import Route


def every_route():
    return [route for router in web.ROUTERS for route in router.routes]


def concrete(path, value=1):
    """A path a route answers, with `value` for each of its parameters."""
    return re.sub(r"\{[^}]*\}", str(value), path)


def paths(value=1):
    """Every route, as (method, a path it answers)."""
    return {(method, concrete(route.path, value)) for route in every_route() for method in route.methods}


def stand_in(monkeypatch, reached):
    """Every route reaches `reached(route, scope, send)`, an async function, and goes no further: not to its model, which a body is
    otherwise held to."""
    async def handle(route, scope, receive, send):
        await reached(route, scope, send)
    monkeypatch.setattr(Route, "handle", handle)


def raise_on_every_route(monkeypatch, make):
    """Every handler raises what `make()` gives. Its model has its say first, so a body must get past it."""
    def boom(*args, **kwargs):
        raise make()
    for route in every_route():
        monkeypatch.setattr(route, "work", boom)
