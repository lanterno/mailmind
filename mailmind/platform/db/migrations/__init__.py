"""The schema's history. A database records the last migration it has run in `PRAGMA
user_version`; `MIGRATIONS` lists (version, migration) in order, and only ever grows at the end:
the version number is the on-disk contract, so a migration is never renumbered or removed.

Migrations 1 to 20 are frozen in `m0001_m0020`. Each later one has a module of its own,
`m<NNNN>_<what>.py`, with a `migrate(c)` that is idempotent and never commits.
"""
from mailmind.platform.db.migrations import m0021_ai_usage
from mailmind.platform.db.migrations.m0001_m0020 import BASE_SCHEMA, LEGACY_COLS, _addcol
from mailmind.platform.db.migrations.m0001_m0020 import MIGRATIONS as _FROZEN

__all__ = ["BASE_SCHEMA", "LEGACY_COLS", "MIGRATIONS", "add_column"]

add_column = _addcol

MIGRATIONS = [
    *_FROZEN,
    (21, m0021_ai_usage.migrate),
]
