"""`python -m mailmind db check-upgrade`: what the next start's migrations would do to a database,
tried on a copy.

It copies the database, read-only, to a temp folder beside it (so the mail stays in the data
directory), migrates the copy the way a start would, and runs SQLite's integrity, foreign-key and
full-text-index checks on it. It prints only table names, row counts and the checks' verdicts,
never a row's contents, so its report can be shared. The database itself is never written; the
copy is deleted afterwards.
"""
import os
import pathlib
import sqlite3
import tempfile

from mailmind.platform.db import migrate

OK, PROBLEM, CANNOT_RUN = 0, 1, 2


def check_upgrade(path, say=print):
    """Check the database at `path` and report through `say`. Returns OK when every check passes
    and every table that was there keeps its row count, PROBLEM otherwise, and CANNOT_RUN when
    there is no database to check."""
    if not os.path.isfile(path):
        say(f"No database at {path}.")
        return CANNOT_RUN
    beside = os.path.dirname(os.path.abspath(path))
    with tempfile.TemporaryDirectory(prefix=".mailmind-check-", dir=beside) as folder:
        copy = os.path.join(folder, "upgrade-copy.db")
        _copy_read_only(path, copy)
        before, v_before = _counts(copy), _version(copy)
        migrate.init(copy)
        after, v_after = _counts(copy), _version(copy)
        checks = _checks(copy)
    say(f"A copy of the database, migrated from version {v_before} to {v_after}:")
    say("")
    width = max([len(t) for t in (*before, *after)] + [5])
    say(f"  {'table':<{width}}  {'before':>10}  {'after':>10}")
    changed = []
    for table in sorted(set(before) | set(after)):
        b, a = before.get(table), after.get(table)
        note = "" if a == b else ("  new" if b is None else "  CHANGED")
        if b is not None and a != b:
            changed.append(table)
        say(f"  {table:<{width}}  {_n(b):>10}  {_n(a):>10}{note}")
    say("")
    for name, verdict in checks:
        say(f"  {name}: {verdict}")
    say(f"  every table kept its rows: {'yes' if not changed else 'no (' + ', '.join(changed) + ')'}")
    return OK if not changed and all(v == "ok" for _, v in checks) else PROBLEM


def _copy_read_only(path, dest):
    source = sqlite3.connect(pathlib.Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        target = sqlite3.connect(dest)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def _query(path, sql):
    c = sqlite3.connect(path)
    try:
        return c.execute(sql).fetchall()
    finally:
        c.close()


def _version(path):
    return _query(path, "PRAGMA user_version")[0][0]


def _counts(path):
    tables = [name for (name,) in _query(path, "SELECT name FROM sqlite_master WHERE type='table' "
                                               "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {t: _query(path, f'SELECT count(*) FROM "{t}"')[0][0] for t in tables}


def _checks(path):
    """(check, verdict) pairs: each verdict "ok", or the number of problems found, never what they
    are about."""
    out = []
    integrity = [r for (r,) in _query(path, "PRAGMA integrity_check")]
    out.append(("integrity_check", "ok" if integrity == ["ok"] else f"{len(integrity)} problem(s)"))
    keys = _query(path, "PRAGMA foreign_key_check")
    out.append(("foreign_key_check", "ok" if not keys else f"{len(keys)} problem(s)"))
    c = sqlite3.connect(path)
    try:
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='msg_fts'").fetchone():
            try:
                c.execute("INSERT INTO msg_fts(msg_fts) VALUES('integrity-check')")
                out.append(("full-text index", "ok"))
            except sqlite3.DatabaseError:
                out.append(("full-text index", "1 problem(s)"))
    finally:
        c.close()
    return out


def _n(count):
    return "-" if count is None else str(count)
