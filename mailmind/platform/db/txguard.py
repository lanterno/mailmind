"""No transaction stays open across a network call.

A write transaction holds SQLite's one write lock until it commits. Held across an IMAP command,
an SMTP session or an HTTP request, it keeps every other writer (a sync, the outbox, a click)
waiting for as long as the server takes, up to the minute's busy timeout, after which they fail
with "database is locked". So code that talks to the network calls `refuse_network(what)` first,
which raises TxAcrossNetworkError if this thread has a transaction open: commit or roll back
before the call.

database.connect tracks each connection it opens for the thread that opened it. A SQLite
connection belongs to that thread, so only the thread's own connections can be in its way.

Standard library only: python3 server.py uses it.
"""
import sqlite3
import threading
import weakref
from collections.abc import Callable


class TxAcrossNetworkError(RuntimeError):
    """A network call made while this thread holds a transaction open."""


class Connection(sqlite3.Connection):
    """sqlite3's connection, which the guard can track without keeping it open."""


_local = threading.local()


def track(conn: Connection) -> None:
    """Count `conn` among this thread's connections."""
    conns = getattr(_local, "conns", None)
    if conns is None:
        conns = _local.conns = weakref.WeakSet()
    conns.add(conn)


def open_transactions() -> int:
    """How many of this thread's connections have a transaction open."""
    n = 0
    for c in list(getattr(_local, "conns", ())):
        try:
            n += c.in_transaction
        except sqlite3.ProgrammingError:               # closed
            pass
    return n


def refuse_network(what: str) -> None:
    """Raise TxAcrossNetworkError, naming `what`, if this thread has a transaction open."""
    if open_transactions():
        raise TxAcrossNetworkError(f"{what} while a transaction is open on this thread")


def guard_calls(obj, name: str, what: str | Callable[..., str]):
    """Make each call of `obj.name` pass refuse_network first, naming the call `what`, or
    `what(*args)` when it is a function. Returns obj. An object without that method (a test's
    stand-in) is left as it is."""
    real = getattr(obj, name, None)
    if real is None:
        return obj

    def guarded(*args, **kw):
        refuse_network(what(*args) if callable(what) else what)
        return real(*args, **kw)
    setattr(obj, name, guarded)
    return obj
