"""Opening a connection to MailMind's SQLite database."""
import sqlite3

from mailmind.platform.db import txguard


def connect(path):
    """A connection to the database at `path`: rows readable by column name, write-ahead logging,
    each commit on the disk itself before it returns, a minute's wait for a lock, and foreign keys
    enforced. txguard tracks it, so a network call made while it has a transaction open raises."""
    c = sqlite3.connect(path, timeout=60, factory=txguard.Connection)
    txguard.track(c)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    # A commit survives a power cut, not only a crash: the outbox's `delivered` and the queue's
    # `applied` are exactly-once gates, and losing one would send or apply something twice. FULL
    # syncs the log at every commit; on macOS a sync reaches the drive's own cache unless
    # fullfsync is on (about half a millisecond a commit; elsewhere it does nothing).
    c.execute("PRAGMA synchronous=FULL")
    c.execute("PRAGMA fullfsync=ON")
    c.execute("PRAGMA busy_timeout=60000")
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA temp_store=MEMORY")   # what a big sort or join holds of the mail never lands in a temp file
    return c
