"""A connection's life, so that no use case runs it by hand: one connection for one block.

    with uow.read() as c:     # no transaction; closed when the block ends
        ...
    with uow.write() as c:    # BEGIN IMMEDIATE; committed if the block ends, gone if it raises; closed either way
        ...

The order is always read and let go, then the network, then a short write. A write block holds SQLite's one write lock from
its first line to its commit, so:

- A network call inside one raises txguard.TxAcrossNetworkError, as it does across any open transaction.
- What it reads before it writes stays true until it commits, which is why a read and the write it decides belong in one
  block when they must be one step. Where they needn't be, and the block often writes nothing, read in a read block first:
  a write block keeps other writers out even if it never writes.
- Another write block on the same thread could only wait for this one to end, so it raises at once instead of waiting out
  the busy timeout. That's a block written wrongly, and no one's lock.

A loop that commits as it goes is one write block per commit, with whatever the network says in between read outside them.

Each block's connection comes from database.connect, so WAL, synchronous=FULL and txguard apply.
"""
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from mailmind.kernel.row import Connection
from mailmind.platform import settings
from mailmind.platform.db import database, driver, txguard


class WroteInARead(RuntimeError):
    """A read block wrote. It is rolled back as the block closes, so nothing it wrote would have lasted."""


class NestedWrite(RuntimeError):
    """A write block opened while this thread holds a transaction open: it would wait for itself."""


def _data_directory() -> driver.Connection:
    return database.connect(settings.current().db)


class UnitOfWork:
    """Blocks on the database `connect` opens, by default the data directory's as it is when each block opens. It holds
    nothing between blocks, so one is as good as another."""

    def __init__(self, connect: Callable[[], driver.Connection] = _data_directory):
        self.connect = connect       # what DurableQueue opens its own connections with

    @contextmanager
    def read(self) -> Iterator[Connection]:
        """A connection to read through, closed when the block ends."""
        c = self.connect()
        try:
            yield c
            if c.in_transaction:
                raise WroteInARead("a read block wrote: use write()")
        finally:
            c.close()

    @contextmanager
    def write(self) -> Iterator[Connection]:
        """A connection holding the write lock: committed if the block ends, a return included, rolled back if it raises,
        and closed."""
        if txguard.open_transactions():
            raise NestedWrite("a write block inside a transaction on this thread")
        c = self.connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            yield c
            c.commit()
        finally:
            c.close()                # what never committed goes with it
