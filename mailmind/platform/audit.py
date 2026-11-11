"""The audit trails, in the database, where they are encrypted (`audit_events`, migration 48). The `sweep` trail says what a
sweep, expiry or a sender's cleanup trashed, and the `reconcile` trail what a reconcile pruned. A record is a JSON object
holding its own `ts`, and a row keeps it as it was written. Each context writes its own trail through an adapter of its own,
and the page reads both as one, newest first (`/api/audit`). Nothing is kept beside the database.

Each function takes its caller's connection and leaves the commit to it: a record written just before a move to Trash is
committed before the move asks the server anything (txguard). Standard library only.
"""
import json
from collections.abc import Iterable
from typing import Any

from mailmind.kernel.row import Connection

SWEEP = "sweep"
RECONCILE = "reconcile"
TRAILS = (SWEEP, RECONCILE)

type Record = dict[str, Any]                  # one event, with its own "ts", as a line of the trail's file held it


def append(c: Connection, trail: str, records: Iterable[Record]) -> None:
    """Add each of `records` to `trail`, in the order given."""
    if trail not in TRAILS:
        raise ValueError(f"no such trail: {trail}")
    for r in records:
        c.execute("INSERT INTO audit_events(trail, ts, body) VALUES(?,?,?)", (trail, r["ts"], json.dumps(r)))


def read(c: Connection, limit: int) -> dict[str, Any]:
    """The newest `limit` records of both trails, newest first and those of one moment in the order they were written, and
    whether there are older ones."""
    n = max(limit, 0)
    rows = c.execute("SELECT body FROM audit_events ORDER BY ts DESC, id LIMIT ?", (n + 1,)).fetchall()
    events = [json.loads(r["body"]) for r in rows]
    return {"events": events[:n], "has_more": len(events) > n}
