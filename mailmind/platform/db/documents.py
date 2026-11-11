"""A document a context keeps whole, as one row of `documents` (migration 48), named for what it holds: `views`, `snippets`.

Its body is JSON in the shape the file it was once kept in held, written as that file was, so the file's text goes in as it
is. Each call runs on the connection it's given and leaves the commit to its caller: a change reads, changes and writes in
one write block, so that two at once lose neither.
"""
import json

from mailmind.kernel.row import Connection
from mailmind.kernel.when import now_iso


def read(c: Connection, name: str) -> object:
    """What the document holds, as JSON parses it: None when there is none yet, or its text won't parse."""
    row = c.execute("SELECT body FROM documents WHERE name=?", (name,)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except ValueError:
        return None


def write(c: Connection, name: str, value: object) -> None:
    """`value` as the whole document, a version on from the last. What JSON can't hold raises before anything is written."""
    body = json.dumps(value, indent=2, ensure_ascii=False)
    c.execute("INSERT INTO documents(name, body, changed_at) VALUES(?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
              "body=excluded.body, version=documents.version+1, changed_at=excluded.changed_at", (name, body, now_iso()))
