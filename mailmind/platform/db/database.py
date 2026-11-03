"""Opening a connection to MailMind's SQLite database."""
import sqlite3


def connect(path):
    """A connection to the database at `path`: rows readable by column name, write-ahead logging,
    a minute's wait for a lock, and foreign keys enforced."""
    c = sqlite3.connect(path, timeout=60)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=60000")
    c.execute("PRAGMA foreign_keys=ON")
    return c
