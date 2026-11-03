"""Bringing a database to the newest schema."""
from mailmind.platform.db import database, migrations


def init(path, steps=None):
    """Create the database at `path` if it is new, and run every migration in `steps` (all of
    them unless given) that it hasn't run yet. Safe on every start: the base schema only creates
    what is missing, and each migration is idempotent."""
    c = database.connect(path)
    c.executescript(migrations.BASE_SCHEMA)
    for table, coldef in migrations.LEGACY_COLS:
        migrations.add_column(c, table, coldef)
    v = c.execute("PRAGMA user_version").fetchone()[0]
    for target, fn in (migrations.MIGRATIONS if steps is None else steps):
        if v < target:
            fn(c)
            c.execute(f"PRAGMA user_version={target}")
            v = target
    c.commit()
    c.close()
