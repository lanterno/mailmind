"""Every route the app has, in whichever table holds it: the handler's, or a router's. What walks the routes asks here, so a route
that moves from one to the other is still walked, with no edit to the test.
"""
import re

from mailmind.bootstrap import web
from mailmind.http.route import Route


def on_routers():
    """Every route that has moved to FastAPI."""
    return [route for router in web.ROUTERS for route in router.routes]


def concrete(path, value=1):
    """A path a route answers, with `value` for each of its parameters."""
    return re.sub(r"\{[^}]*\}", str(value), path)


def by_name(value=1):
    """Every route that names its path, in either table, as (method, a path it answers)."""
    return {*web.H.routes._exact, *((method, concrete(route.path, value)) for route in on_routers() for method in route.methods)}


def shapes_on_the_handler():
    """The handler's routes that match by shape and so name no path, as (method, name). One that moves names its path above."""
    return {(method, route.__name__) for method, _, route in web.H.routes._matching}


def stand_in_on_routers(monkeypatch, reached):
    """Every route on a router reaches `reached(route, scope, send)`, an async function, and goes no further: not to its model,
    which a body is otherwise held to."""
    async def handle(route, scope, receive, send):
        await reached(route, scope, send)
    monkeypatch.setattr(Route, "handle", handle)


def raise_on_routers(monkeypatch, make):
    """Every handler on a router raises what `make()` gives. Its model has its say first, so a body must get past it."""
    def boom(*args, **kwargs):
        raise make()
    for route in on_routers():
        monkeypatch.setattr(route, "work", boom)
