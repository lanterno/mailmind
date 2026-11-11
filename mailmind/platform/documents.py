"""A document a context keeps whole, in the database's `documents` table (migration 48): one row a name, its body the JSON its
file held. `version` goes up with each write, so a reader that keeps a document in memory asks only that to know whether it is
still current.

Every function here takes its caller's connection and leaves the commit to it, so a read, a change and the write that keeps it
are one transaction when the caller makes them one (`UnitOfWork.write`). Standard library only.
"""
from typing import NamedTuple

from mailmind.kernel.row import Connection
from mailmind.kernel.when import now_iso


class Document(NamedTuple):
    body: str
    version: int


def version(c: Connection, name: str) -> int | None:
    """The document's version, or None when there is none yet."""
    row = c.execute("SELECT version FROM documents WHERE name=?", (name,)).fetchone()
    return None if row is None else row["version"]


def read(c: Connection, name: str) -> Document | None:
    """The document as it stands, or None when there is none yet."""
    row = c.execute("SELECT body, version FROM documents WHERE name=?", (name,)).fetchone()
    return None if row is None else Document(row["body"], row["version"])


def write(c: Connection, name: str, body: str) -> None:
    """Put `body` in place of what the document held, a version on: the first is 1."""
    c.execute("""INSERT INTO documents(name, body, version, changed_at) VALUES(?, ?, 1, ?)
                 ON CONFLICT(name) DO UPDATE SET body=excluded.body, version=documents.version + 1,
                                                 changed_at=excluded.changed_at""", (name, body, now_iso()))
