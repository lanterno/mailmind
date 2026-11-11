"""Copies of the database taken before its schema changes.

Each is a consistent snapshot, taken online with SQLite's backup API, so a write-ahead log not yet
checkpointed is in it, and encrypted with the database's key as the database is. It goes to `backups/` beside the database, readable by its owner only, as
`mailmind-v{version}-{UTC time}.db`; only the newest KEEP are kept. To roll back, stop MailMind
and copy one over the database (and delete the database's -wal and -shm files).
"""
import glob
import os
import re
import time

from mailmind.platform.db import database

KEEP = 3
FOLDER = "backups"


def take(c, version):
    """Back up the database `c` is open on, at schema `version`, and prune the oldest copies.
    Returns the new copy's path."""
    path = c.execute("PRAGMA database_list").fetchone()[2]
    folder = os.path.join(os.path.dirname(path), FOLDER)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    dest = _new_file(folder, f"mailmind-v{int(version)}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
    copy = database.keyed(dest)              # encrypted with the database's own key
    try:
        c.backup(copy)
    finally:
        copy.close()
    _prune(folder)
    return dest


def _new_file(folder, stem):
    """A new, empty file for a backup, created readable by its owner only (never another's file)."""
    for n in range(100):
        dest = os.path.join(folder, stem + (f"-{n}" if n else "") + ".db")
        try:
            os.close(os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            return dest
        except FileExistsError:
            continue
    raise FileExistsError(f"no free backup name for {stem} in {folder}")


def _prune(folder):
    copies = sorted(glob.glob(os.path.join(folder, "mailmind-v*.db")), key=_age)
    for old in copies[:-KEEP]:
        os.remove(old)


def _age(path):
    """Oldest first: by modification time, then, for copies named in the same second, by the
    number _new_file added to the name."""
    n = re.search(r"Z-(\d+)\.db$", path)
    return os.path.getmtime(path), int(n.group(1)) if n else 0
