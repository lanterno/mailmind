"""Bringing a database to the newest schema.

The base schema and each migration run in a transaction of their own, with the migration's new
version set in the same transaction: a migration that fails leaves the database exactly as it was
before it, at the version before it, and the next start runs it again. Before it migrates a
database that holds anything, it backs it up (mailmind.platform.db.backup).
"""
import sqlite3

from mailmind.platform.db import backup, database, migrations


def init(path, steps=None):
    """Create the database at `path` if it is new, and run every migration in `steps` (all of
    them unless given) that it hasn't run yet. Safe on every start: each migration is idempotent,
    and a database already at the last of them is left alone. The base schema only creates what is
    missing, but that includes a table a later migration retired, so it runs only on a database
    older than the frozen migrations' last, which it was written for, and never brings one back."""
    steps = migrations.MIGRATIONS if steps is None else steps
    c = database.connect(path)
    c.isolation_level = None                    # the transactions below are the only ones
    try:
        v = c.execute("PRAGMA user_version").fetchone()[0]
        pending = any(target > v for target, _ in steps)
        if v and not pending:
            return
        if pending and c.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]:
            backup.take(c, v)

        def base(db):
            db.executescript(migrations.BASE_SCHEMA)
            for table, coldef in migrations.LEGACY_COLS:
                migrations.add_column(db, table, coldef)
        if v < migrations.BASE_UNTIL:
            _in_transaction(c, base)
        for target, fn in steps:
            if v < target:
                _in_transaction(c, fn, version=target)
                v = target
    finally:
        c.close()


def _in_transaction(c, fn, version=None):
    """Run `fn` on `c` in one write transaction, setting the version too when one is given."""
    c.execute("BEGIN IMMEDIATE")
    try:
        fn(_Within(c))
        if version is not None:
            c.execute(f"PRAGMA user_version={int(version)}")
        c.execute("COMMIT")
    except BaseException:
        c.execute("ROLLBACK")
        raise


class _Within:
    """The connection as a migration sees it. sqlite3's executescript commits whatever
    transaction is open before it runs, so here a script runs one statement at a time instead,
    inside the migration's transaction."""

    def __init__(self, c):
        self._c = c

    def execute(self, sql, params=()):
        return self._c.execute(sql, params)

    def executescript(self, script):
        for statement in _statements(script):
            self._c.execute(statement)


def _statements(script):
    """The statements in an SQL script, one at a time. A semicolon inside a string or a trigger's
    body doesn't end one: sqlite3.complete_statement decides where each ends."""
    pending = ""
    for piece in script.split(";"):
        pending += piece + ";"
        if sqlite3.complete_statement(pending):
            if pending.strip(" \t\r\n;"):
                yield pending
            pending = ""
