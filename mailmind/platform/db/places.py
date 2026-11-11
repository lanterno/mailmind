"""Where the page is, kept out of the browser: a place is one row of `places` (migration 49), a route the page would otherwise
write into its URL under a random id the URL names instead, `/p/<id>`.

The browser keeps every URL a tab goes to in its history, in plaintext, and may sync it, so a URL that named an account, a
folder, a search, a topic or an opened message would keep it there. The page makes each id itself (`web/src/app/route.ts`), so
going somewhere waits for nothing, and sends the places it made here in a batch (`POST /api/places`). Opening `/p/<id>` reads
the route back into the page (`routers/static.py`). What a route holds is the page's to read: here it is a JSON object of at
most `MOST_BYTES`, kept as it came. Only the `KEPT` used last stay, and a place pruned opens as the app's home.

`remember` and `recall` take their caller's connection and leave the commit to it; `keep` and `opened` are each one block.
"""
import json
import re
from collections.abc import Sequence

from mailmind.kernel.errors import Refused
from mailmind.kernel.row import Connection
from mailmind.kernel.when import now_iso
from mailmind.platform.db.uow import UnitOfWork

ID = re.compile(r"[A-Za-z0-9_-]{16,32}")    # what the page makes: 16 random bytes, base64url
MOST_BYTES = 4096                           # a route's JSON, as kept
MOST_AT_ONCE = 64                           # the places one request names: the page sends more as more requests
KEPT = 10_000                               # the places used last, kept

_uow = UnitOfWork()


def text_of(route: object) -> str:
    """A route's JSON as kept: compact, and a person's own words as they are."""
    return json.dumps(route, ensure_ascii=False, separators=(",", ":"))


def remember(c: Connection, given: Sequence[tuple[object, object]]) -> None:
    """Keep each (id, route) given, a route sent again under its id replacing what it held, then prune to the `KEPT` newest.
    One that can't be kept refuses the whole batch, before anything is written."""
    if len(given) > MOST_AT_ONCE:
        raise Refused(f"at most {MOST_AT_ONCE} places at once")
    rows = []
    for place, route in given:
        if not isinstance(place, str) or not ID.fullmatch(place):
            raise Refused("a place's id is 16 to 32 letters, digits, - or _")
        if not isinstance(route, dict):
            raise Refused("a place's route is a JSON object")
        text = text_of(route)
        if len(text.encode("utf-8")) > MOST_BYTES:
            raise Refused(f"a place's route is at most {MOST_BYTES} bytes")
        rows.append((place, text))
    now = now_iso()
    for place, text in rows:
        c.execute("INSERT INTO places(id, route, used_at) VALUES(?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                  "route=excluded.route, used_at=excluded.used_at", (place, text, now))
    if rows:
        c.execute("DELETE FROM places WHERE id IN (SELECT id FROM places ORDER BY used_at DESC, rowid DESC LIMIT -1 OFFSET ?)",
                  (KEPT,))


def recall(c: Connection, place: str) -> str | None:
    """The route's JSON text a place holds, marked used now, or None for an id that names none."""
    if not ID.fullmatch(place):
        return None
    row = c.execute("SELECT route FROM places WHERE id=?", (place,)).fetchone()
    if row is None:
        return None
    c.execute("UPDATE places SET used_at=? WHERE id=?", (now_iso(), place))
    return row[0]


def keep(given: Sequence[tuple[object, object]]) -> None:
    """`remember`, in a write of its own."""
    with _uow.write() as c:
        remember(c, given)


def opened(place: str) -> str | None:
    """`recall`, in a write of its own when the id could name a place, and nothing asked of the database when it couldn't."""
    if not ID.fullmatch(place):
        return None
    with _uow.write() as c:
        return recall(c, place)
