"""`python -m mailmind db check-upgrade`: what the next start's migrations would do to a database,
tried on a copy.

It copies the database, read-only, to a temp folder beside it (so the mail stays in the data
directory), migrates the copy the way a start would, and runs SQLite's integrity, foreign-key and
full-text-index checks on it. It prints only table names, row counts and the checks' verdicts,
never a row's contents, so its report can be shared. The database itself is never written; the
copy is deleted afterwards.

A row that points at a row that's gone (a body or summary whose message was deleted while an
older MailMind left foreign keys off) is the database's, not the upgrade's: the foreign-key check
counts what was there before the migrations too, and fails only on what they add.

A table a migration retires on purpose (`migrations.RETIRED`) is no loss, and the report says so. Nor
is a change in the count of one it builds afresh (`migrations.REBUILT`); the full-text check says whether
that one holds every message. Nor is the growth of a table a migration carries something over to
(`migrations.FILLED`), though its shrinking would be. Nor are exactly the rows a migration takes on purpose
(`migrations.TRIMMED`), counted before it runs, and one row more is. The index's own tables (`msg_fts_data` and the like) aren't listed.
"""
import collections
import os
import pathlib
import tempfile

from mailmind.platform.db import driver, migrate, migrations

OK, PROBLEM, CANNOT_RUN = 0, 1, 2


def check_upgrade(path, say=print):
    """Check the database at `path` and report through `say`. Returns OK when every check passes
    and every table that was there keeps its row count (a table the migrations retire aside),
    PROBLEM otherwise, and CANNOT_RUN when there is no database to check."""
    if not os.path.isfile(path):
        say(f"No database at {path}.")
        return CANNOT_RUN
    beside = os.path.dirname(os.path.abspath(path))
    with tempfile.TemporaryDirectory(prefix=".mailmind-check-", dir=beside) as folder:
        copy = os.path.join(folder, "upgrade-copy.db")
        _copy_read_only(path, copy)
        before, v_before, keys_before = _counts(copy), _version(copy), _key_problems(copy)
        to_go = _rows_to_go(copy, v_before, before)
        migrate.init(copy)
        after, v_after = _counts(copy), _version(copy)
        checks = _checks(copy, keys_before)
    say(f"A copy of the database, migrated from version {v_before} to {v_after}:")
    say("")
    width = max([len(t) for t in (*before, *after)] + [5])
    say(f"  {'table':<{width}}  {'before':>10}  {'after':>10}")
    changed = []
    for table in sorted(set(before) | set(after)):
        b, a = before.get(table), after.get(table)
        retired = a is None and migrations.RETIRED.get(table, 0) > v_before
        rebuilt = migrations.REBUILT.get(table, 0) > v_before
        filled = b is not None and a is not None and a > b and migrations.FILLED.get(table, 0) > v_before
        trimmed = b is not None and a is not None and a != b and a == b - to_go.get(table, 0)
        note = ("" if a == b else "  new" if b is None else "  retired" if retired
                else "  rebuilt" if rebuilt else "  carried over" if filled else "  trimmed" if trimmed else "  CHANGED")
        if b is not None and a != b and not retired and not rebuilt and not filled and not trimmed:
            changed.append(table)
        say(f"  {table:<{width}}  {_n(b):>10}  {_n(a):>10}{note}")
    say("")
    for name, verdict, _ in checks:
        say(f"  {name}: {verdict}")
    say(f"  every table kept its rows: {'yes' if not changed else 'no (' + ', '.join(changed) + ')'}")
    return OK if not changed and all(passed for *_, passed in checks) else PROBLEM


def _copy_read_only(path, dest):
    source = driver.connect(pathlib.Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        target = driver.connect(dest)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def _query(path, sql):
    c = driver.connect(path)
    try:
        return c.execute(sql).fetchall()
    finally:
        c.close()


def _version(path):
    return _query(path, "PRAGMA user_version")[0][0]


def _counts(path):
    tables = [name for (name,) in _query(path, "SELECT name FROM sqlite_master WHERE type='table' "
                                               "AND name NOT LIKE 'sqlite_%' AND name NOT GLOB 'msg_fts_*' "
                                               "ORDER BY name")]
    return {t: _query(path, f'SELECT count(*) FROM "{t}"')[0][0] for t in tables}


def _rows_to_go(path, version, tables):
    """What the migrations still to run take from a table on purpose, by table."""
    return {table: _query(path, count)[0][0] for table, (by, count) in migrations.TRIMMED.items()
            if version < by and table in tables}


def _key_problems(path):
    """How many rows point at a row that's gone, by (table, the table it points at)."""
    return collections.Counter((table, parent) for table, _, parent, _ in
                               _query(path, "PRAGMA foreign_key_check"))


def _by_table(problems):
    counts = collections.Counter()
    for (table, _), n in problems.items():
        counts[table] += n
    return ", ".join(f"{table} {n}" for table, n in sorted(counts.items()))


def _checks(path, keys_before):
    """(check, verdict, passed) triples: each verdict "ok", or the number of problems found, and
    for foreign keys the tables they're in, never what they are about. `keys_before` is what
    _key_problems found before the migrations."""
    out = []
    integrity = [r for (r,) in _query(path, "PRAGMA integrity_check")]
    out.append(("integrity_check", "ok" if integrity == ["ok"] else f"{len(integrity)} problem(s)",
                integrity == ["ok"]))
    keys = _key_problems(path)
    added = keys - keys_before
    total = sum(keys.values())
    if not total:
        verdict = "ok"
    elif not added:
        verdict = f"{total} problem(s), all there before the upgrade ({_by_table(keys)})"
    else:
        verdict = f"{total} problem(s), {sum(added.values())} of them new ({_by_table(added)})"
    out.append(("foreign_key_check", verdict, not added))
    c = driver.connect(path)
    try:
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='msg_fts'").fetchone():
            try:
                c.execute("INSERT INTO msg_fts(msg_fts) VALUES('integrity-check')")
                missing = c.execute("SELECT count(*) FROM messages WHERE pk NOT IN "
                                    "(SELECT rowid FROM msg_fts)").fetchone()[0]
                out.append(("full-text index", f"{missing} message(s) not in it" if missing else "ok", not missing))
            except driver.DatabaseError:
                out.append(("full-text index", "1 problem(s)", False))
    finally:
        c.close()
    return out


def _n(count):
    return "-" if count is None else str(count)
