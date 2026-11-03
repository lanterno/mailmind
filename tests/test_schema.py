"""The database schema: a fresh store.init(), an upgrade from the version before the last migration,
and init() run again must all give the schema in tests/golden/schema_v20.sql (rewritten by
`pytest --update-golden`). Every migration must also be safe to run twice, since init() runs on
every start. These run on empty temporary databases only.

When a migration is added, the file's name changes with the version: rename it in the same commit
as the migration, and the diff shows exactly what the migration did to the schema.
"""
import os
import sqlite3

import pytest

HERE = os.path.dirname(__file__)


def version():
    import store
    return store._MIGRATIONS[-1][0]


def snapshot_path():
    return os.path.join(HERE, "golden", f"schema_v{version()}.sql")


def dump(path):
    """The schema as SQL text: every table, index, trigger and view, in name order, then the
    version. Whitespace inside a statement is collapsed so formatting edits don't count."""
    c = sqlite3.connect(path)
    try:
        rows = c.execute("SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL "
                         "AND name NOT LIKE 'sqlite_%' ORDER BY type, name").fetchall()
        v = c.execute("PRAGMA user_version").fetchone()[0]
    finally:
        c.close()
    out = [f"-- {kind} {name}\n{' '.join(sql.split())};\n" for kind, name, sql in rows]
    return "\n".join(out) + f"\nPRAGMA user_version={v};\n"


@pytest.fixture
def fresh_db(data_dir):
    """A path for a new, empty database that store.conn() will open: mailmind.db in a data
    directory of its own, made the app's (data_dir puts its own back after the test)."""
    from mailmind.platform import settings
    n = iter(range(100))

    def make():
        home = data_dir / f"schema-{next(n)}"
        home.mkdir()
        settings.configure(settings.Settings(home=str(home)))
        return settings.current().db
    return make


def test_a_fresh_database_has_the_checked_in_schema(fresh_db, request):
    import store
    path = fresh_db()
    store.init()
    got = dump(path)
    if request.config.getoption("--update-golden"):
        with open(snapshot_path(), "w", encoding="utf-8") as f:
            f.write(got)
    assert os.path.exists(snapshot_path()), f"no {os.path.relpath(snapshot_path())}; run pytest --update-golden"
    with open(snapshot_path(), encoding="utf-8") as f:
        assert got == f.read(), f"schema differs from {os.path.relpath(snapshot_path())}"


def test_an_upgrade_from_the_previous_version_matches_a_fresh_database(fresh_db, monkeypatch):
    import store
    fresh = fresh_db()
    store.init()
    old = fresh_db()
    with monkeypatch.context() as m:
        m.setattr(store, "_MIGRATIONS", store._MIGRATIONS[:-1])
        store.init()
    assert dump(old).endswith(f"PRAGMA user_version={version() - 1};\n")
    store.init()
    assert dump(old) == dump(fresh)


def test_init_again_keeps_the_schema_and_the_data(fresh_db):
    import store
    path = fresh_db()
    store.init()
    before = dump(path)
    c = store.conn()
    c.execute("INSERT INTO muted(account, thread_id, muted_at) VALUES ('ada@example.test', 't1', 'now')")
    c.commit()
    c.close()
    store.init()
    assert dump(path) == before
    c = store.conn()
    try:
        assert c.execute("SELECT thread_id FROM muted").fetchall()[0][0] == "t1"
    finally:
        c.close()


def test_every_migration_can_run_twice(fresh_db):
    import store
    path = fresh_db()
    store.init()
    before = dump(path)
    c = store.conn()
    try:
        for _, fn in store._MIGRATIONS:
            fn(c)
            c.commit()
    finally:
        c.close()
    assert dump(path) == before


def test_migration_versions_only_go_up_by_one():
    import store
    assert [t for t, _ in store._MIGRATIONS] == list(range(1, version() + 1))
