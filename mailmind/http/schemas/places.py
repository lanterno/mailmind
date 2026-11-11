"""What the page sends of the places its URLs name, and what it is answered (`mailmind.platform.db.places`)."""
from typing import Any, Literal

from mailmind.http.schemas.common import Answer, RequestBody


class Place(RequestBody):
    """A route the page wrote as `/p/<id>`: the id it made, and the route as an object, which only the page reads."""
    id: str
    route: dict[str, Any]


class PlacesBody(RequestBody):
    """The places the page made since it last sent any. The store words what it won't keep: an id the page couldn't have made,
    a route too long, or more at once than it takes."""
    places: list[Place]


class PlacesKept(Answer):
    ok: Literal[True]
