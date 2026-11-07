"""Opening a connection to MailMind's SQLite database."""
import os
import sqlite3

from mailmind.platform.db import txguard


def keep_private(path):
    """The database holds every message, so only its owner may read it, and its log and index beside it, which SQLite makes
    with the database's own mode only when they are new. What isn't there, or can't be changed, is left to SQLite to say."""
    for each in (path, path + "-wal", path + "-shm"):
        try:
            os.chmod(each, 0o600)
        except OSError:
            pass


def connect(path):
    """A connection to the database at `path`, which is its owner's alone if this makes it: rows readable by column name,
    write-ahead logging, each commit on the disk itself before it returns, a minute's wait for a lock, and foreign keys
    enforced. txguard tracks it, so a network call made while it has a transaction open raises."""
    try:
        os.close(os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600))
    except OSError:
        pass                                    # there already, or nowhere to put it: SQLite says which
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
