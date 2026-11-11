"""mailmind.platform.db.backup, and when migrate.init takes a backup: before it changes the schema
of a database that holds anything, and at no other time. Fictional data in temp directories only."""
import os
import stat

import pytest

from mailmind.platform.db import backup, database, migrate, migrations

NEWEST = migrations.MIGRATIONS[-1][0]


def database_at(folder, version):
    """mailmind.db in `folder`, migrated to `version` and holding one muted thread."""
    path = str(folder / "mailmind.db")
    migrate.init(path, migrations.MIGRATIONS[:version])
    c = database.keyed(path)
    c.execute("INSERT INTO muted(account, thread_id, muted_at) VALUES ('ada@example.test', 't1', 'now')")
    c.commit()
    c.close()
    return path


def read(path, sql):
    c = database.keyed(path)
    try:
        return c.execute(sql).fetchall()
    finally:
        c.close()


def backups(folder):
    d = folder / backup.FOLDER
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_an_upgrade_backs_the_database_up_first(tmp_path):
    path = database_at(tmp_path, NEWEST - 1)
    assert backups(tmp_path) == []                          # it was new: nothing to back up
    migrate.init(path)
    [name] = backups(tmp_path)
    assert name.startswith(f"mailmind-v{NEWEST - 1}-") and name.endswith("Z.db")
    copy = tmp_path / backup.FOLDER / name
    assert mode(copy) == 0o600 and mode(tmp_path / backup.FOLDER) == 0o700
    assert read(copy, "PRAGMA user_version") == [(NEWEST - 1,)]
    assert read(copy, "SELECT thread_id FROM muted") == [("t1",)]
    assert read(path, "PRAGMA user_version") == [(NEWEST,)]


def test_a_new_or_current_database_takes_no_backup(tmp_path):
    path = str(tmp_path / "mailmind.db")
    migrate.init(path)
    migrate.init(path)
    assert backups(tmp_path) == []


def test_only_the_newest_three_are_kept(tmp_path):
    path = database_at(tmp_path, NEWEST)
    c = database.keyed(path)
    try:
        made = [os.path.basename(backup.take(c, NEWEST)) for _ in range(5)]
    finally:
        c.close()
    assert backups(tmp_path) == sorted(made[-backup.KEEP:])


def test_without_a_backup_nothing_is_migrated(tmp_path):
    path = database_at(tmp_path, NEWEST - 1)
    (tmp_path / backup.FOLDER).write_text("not a folder")
    with pytest.raises(OSError):
        migrate.init(path)
    assert read(path, "PRAGMA user_version") == [(NEWEST - 1,)]
