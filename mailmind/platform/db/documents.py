"""A document a context keeps whole, as one row of `documents` (migration 48), named for what it holds: `rules`, `views`,
`snippets`.

Its body is JSON in the shape the file it was once kept in held, written as that file was, so the file's text goes in as it
is. `version` goes up with each write, so a reader that keeps a document in memory asks only that to know whether it is still
current. Each call runs on the connection it's given and leaves the commit to its caller: a change reads, changes and writes in
one write block, so that two at once lose neither.
"""
import json
from typing import NamedTuple

from mailmind.kernel.row import Connection
from mailmind.kernel.when import now_iso


class Stored(NamedTuple):
    """A document's text as written, and its version."""
    body: str
    version: int


def version(c: Connection, name: str) -> int | None:
    """The document's version, None when there is none yet."""
    row = c.execute("SELECT version FROM documents WHERE name=?", (name,)).fetchone()
    return None if row is None else row[0]


def stored(c: Connection, name: str) -> Stored | None:
    """The document's text and version, None when there is none yet: for a reader to whom a body that won't parse is an
    error, not an empty document (the rule book, whose keep list a sweep must never lose)."""
    row = c.execute("SELECT body, version FROM documents WHERE name=?", (name,)).fetchone()
    return None if row is None else Stored(row[0], row[1])


def read(c: Connection, name: str) -> object:
    """What the document holds, as JSON parses it: None when there is none yet, or its text won't parse."""
    found = stored(c, name)
    if found is None:
        return None
    try:
        return json.loads(found.body)
    except ValueError:
        return None


def write(c: Connection, name: str, value: object) -> None:
    """`value` as the whole document, a version on from the last. What JSON can't hold raises before anything is written."""
    body = json.dumps(value, indent=2, ensure_ascii=False)
    c.execute("INSERT INTO documents(name, body, changed_at) VALUES(?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
              "body=excluded.body, version=documents.version+1, changed_at=excluded.changed_at", (name, body, now_iso()))
