"""The places the page's URLs name, kept in the database so that no URL names anything of a person's
(`mailmind.platform.db.places`). Opening one is the page's own route, in `static.py`."""
from mailmind.http import route
from mailmind.http.schemas.common import answers
from mailmind.http.schemas.places import PlacesBody, PlacesKept
from mailmind.platform.db import places


def router():
    r = route.router()

    @r.post("/api/places", responses=answers(PlacesKept))
    def keep(body: PlacesBody):
        places.keep([(place.id, place.route) for place in body.places])
        return {"ok": True}

    return r
