"""`python -m mailmind db compact`: give back the room a migration or a cleanup freed.

SQLite keeps the pages a deletion frees for the next rows, so the file never shrinks by itself.
VACUUM writes the database afresh. It needs room for a second copy, and nothing else can use the
database while it runs, so only the owner asks for it, with MailMind stopped. It prints only sizes.
"""
import os
import shutil

from mailmind.platform.db import database, driver

OK, PROBLEM, CANNOT_RUN = 0, 1, 2
WAIT_SECONDS = 5       # for another process to let go, before it says the database is in use


def compact(path, say=print):
    """Compact the database at `path` and report through `say`. Returns OK when it did, PROBLEM when
    it couldn't, and CANNOT_RUN when there is no database."""
    if not os.path.isfile(path):
        say(f"No database at {path}.")
        return CANNOT_RUN
    c = database.connect(path)
    c.isolation_level = None
    try:
        c.execute(f"PRAGMA busy_timeout={WAIT_SECONDS * 1000}")
        page = c.execute("PRAGMA page_size").fetchone()[0]
        pages = c.execute("PRAGMA page_count").fetchone()[0]
        free = c.execute("PRAGMA freelist_count").fetchone()[0]
        kept = (pages - free) * page
        room = shutil.disk_usage(os.path.dirname(os.path.abspath(path))).free
        if room < 2 * kept:         # the new copy, and the same again in the write-ahead log
            say(f"This needs {_mb(2 * kept)} free on the disk, and there is {_mb(room)}. Nothing changed.")
            return PROBLEM
        try:
            c.execute("VACUUM")
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except driver.OperationalError:
            say("The database is in use. Stop MailMind and try again. Nothing changed.")
            return PROBLEM
        say(f"The database went from {_mb(pages * page)} to {_mb(os.path.getsize(path))}.")
        return OK
    finally:
        c.close()


def _mb(size):
    return f"{size / 1_000_000:.0f} MB"
