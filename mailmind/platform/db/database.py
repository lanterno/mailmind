"""Opening a connection to MailMind's SQLite database, which is encrypted (mailmind.platform.db.key).

Every connection is keyed before anything else reads it. A plaintext file, a database from before MailMind encrypted
its data, is refused and never opened as it is.
"""
import os
import pathlib

from mailmind.platform.db import driver, key, txguard

PLAINTEXT_HEADER = b"SQLite format 3\x00"       # what a plaintext database begins with; an encrypted one is noise


class NotEncrypted(RuntimeError):
    """The database is a plaintext file: MailMind opens it only encrypted."""

    def __init__(self, path):
        super().__init__(f"The database at {path} isn't encrypted: it is from before MailMind encrypted its data, "
                         "and MailMind opens a database only encrypted.")


class KeyDoesNotFit(RuntimeError):
    """The key opens nothing in this file: another key made it, or it isn't a database."""

    def __init__(self, path):
        super().__init__(f"The database key doesn't open {path}: it was encrypted with another key, or it is no "
                         "database. Nothing was changed.")


def plaintext(path):
    """Whether the file at `path` is a plaintext SQLite database."""
    try:
        with open(path, "rb") as f:
            return f.read(len(PLAINTEXT_HEADER)) == PLAINTEXT_HEADER
    except OSError:
        return False


def keyed(path, with_key=None, read_only=False, **kwargs):
    """A bare connection to the encrypted database file at `path`, keyed with `with_key` (the database key unless
    given) and checked: for a copy, a backup or a check, where `connect`'s settings aren't wanted. `read_only` opens it
    so that nothing can write through it; `kwargs` go to driver.connect. Raises NotEncrypted on a plaintext file, and
    KeyDoesNotFit when the key opens nothing in it."""
    if plaintext(path):
        raise NotEncrypted(path)
    # A URI escapes the path: a #, ? or % in the data directory's name would end or bend it.
    where = pathlib.Path(path).resolve().as_uri() + "?mode=ro" if read_only else path
    c = driver.connect(where, uri=read_only, **kwargs)
    try:
        c.execute(key.pragma(with_key or _key_for(path)))
        c.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except driver.DatabaseError:
        c.close()
        raise KeyDoesNotFit(path) from None
    except BaseException:
        c.close()
        raise
    return c


def _key_for(path):
    """The database key: made now if this file is new and there is none yet."""
    try:
        new = os.path.getsize(path) == 0
    except OSError:
        new = True
    found = key.ensure() if new else key.existing()
    if found is None:
        raise key.KeyProblem(f"The database at {path} is encrypted, and its key isn't in the Keychain. Without it the "
                             "database can't be read.")
    return found


def keep_private(path):
    """The database holds every message, so only its owner may read it, and its log and index beside it, which SQLite makes
    with the database's own mode only when they are new. What isn't there, or can't be changed, is left to SQLite to say."""
    for each in (path, path + "-wal", path + "-shm"):
        try:
            os.chmod(each, 0o600)
        except OSError:
            pass


def connect(path):
    """A keyed connection to the database at `path`, which is its owner's alone if this makes it: rows readable by column name,
    write-ahead logging, each commit on the disk itself before it returns, a minute's wait for a lock, and foreign keys
    enforced. txguard tracks it, so a network call made while it has a transaction open raises."""
    try:
        os.close(os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600))
    except (FileExistsError, FileNotFoundError, NotADirectoryError):
        pass                                    # there already, or nowhere to put it: SQLite says which
    c = keyed(path, timeout=60, factory=txguard.Connection)
    txguard.track(c)
    c.row_factory = driver.Row
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
