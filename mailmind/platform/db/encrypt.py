"""`python -m mailmind db encrypt`: encrypt a database that is still plaintext, with MailMind stopped.

It takes the database for itself first, so nothing else can write to it meanwhile, and says so and changes nothing
if something else has it open. It writes an encrypted copy beside it with SQLCipher's export, checks the copy (SQLite's
integrity check, every table's row count, the schema's version, the foreign keys and the full-text index against the
database's own), and only then puts the copy in the database's place, in one rename. Each backup in `backups/` beside
it that is still plaintext is encrypted the same way, checked before it replaces itself. The key is the database
key (mailmind.platform.db.key), made and kept in the Keychain if there is none yet.

`--dry` does it all on a copy in a temp folder beside the database, with a key made for the copy and thrown away:
nothing is replaced, the Keychain isn't asked, and the copy is deleted. Like `db check-upgrade`, both print only
table names, row counts and the checks' verdicts, never a row, so the report can be shared.

What it can't take back is what the plaintext file left on the disk: the rename frees its blocks, which hold the
mail until something is written over them. FileVault encrypts the whole disk, those blocks too.
"""
import glob
import os
import pathlib
import secrets
import tempfile

from mailmind.platform import environment
from mailmind.platform.db import backup, database, driver, key

OK, PROBLEM, CANNOT_RUN = 0, 1, 2
WAIT_SECONDS = 5        # for another process to let go, before it says the database is in use
SUFFIX = ".encrypting"  # the encrypted copy, beside what it will replace


class InUse(RuntimeError):
    """Something else has the database open."""


def encrypt(path, say=print, dry=False):
    """Encrypt the database at `path` (and its plaintext backups), or with `dry` a copy of it, and report through
    `say`. Returns OK when it did (or there was nothing to do), PROBLEM when a check failed or the database is in use,
    with nothing changed, and CANNOT_RUN when there is no database."""
    if not os.path.isfile(path):
        say(f"No database at {path}.")
        return CANNOT_RUN
    backups = sorted(p for p in glob.glob(os.path.join(os.path.dirname(os.path.abspath(path)), backup.FOLDER, "*.db"))
                     if database.plaintext(p))
    if not database.plaintext(path) and not backups:
        say("The database is encrypted already, and so is every backup beside it. Nothing changed.")
        return OK
    if dry:
        return _dry_run(path, backups, say)
    with_key = key.ensure()
    code = OK
    if database.plaintext(path):
        try:
            code = _encrypt_in_place(path, with_key, say, "The database")
        except InUse:
            say("The database is in use. Stop MailMind and try again. Nothing changed.")
            return PROBLEM
    for each in backups:
        if code == OK:
            code = _encrypt_in_place(each, with_key, say, f"The backup {os.path.basename(each)}")
    if code == OK:
        say("")
        where = (f"the one {environment.DB_KEY.name} gives" if environment.DB_KEY.value()
                 else "in the login Keychain (MailMind's item, `mailmind`)")
        say(f"Done. Its key is {where}: without it the database can't be read.")
    return code


def _dry_run(path, backups, say):
    beside = os.path.dirname(os.path.abspath(path))
    with tempfile.TemporaryDirectory(prefix=".mailmind-encrypt-", dir=beside) as folder:
        copy = os.path.join(folder, "plaintext-copy.db")
        if database.plaintext(path):
            _copy_plaintext(path, copy)
            code = _encrypt_in_place(copy, secrets.token_bytes(key.SIZE), say, "A copy of the database", replace=False)
        else:
            say("The database is encrypted already.")
            code = OK
    say(f"The backups still plaintext, which a run would encrypt too: {len(backups)}.")
    say("Nothing was changed, and the Keychain wasn't asked.")
    return code


def _copy_plaintext(path, dest):
    """A consistent copy of the plaintext database at `path`, read through SQLite read-only, so a write-ahead log not
    yet checkpointed is in it and the database itself is never written."""
    source = driver.connect(pathlib.Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        target = driver.connect(dest)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def _encrypt_in_place(path, with_key, say, what, replace=True):
    """Encrypt the plaintext database at `path` into a copy beside it, check the copy, and put it in its place (unless
    not `replace`, when the copy is only checked and deleted). Raises InUse, with nothing changed, when something else
    has it open."""
    target = path + SUFFIX
    for stale in (target, target + "-journal", target + "-wal", target + "-shm"):
        if os.path.exists(stale):
            os.unlink(stale)                    # an earlier run's, which stopped before its rename
    source = driver.connect(path, timeout=WAIT_SECONDS)
    source.isolation_level = None
    try:
        source.execute(f"PRAGMA busy_timeout={WAIT_SECONDS * 1000}")
        try:
            # Leaving write-ahead logging needs the database to itself, and folds the log in; holding it
            # exclusively from the first read on keeps every other writer out until the rename.
            mode = source.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
            if str(mode).lower() != "delete":
                raise InUse()
            source.execute("PRAGMA locking_mode=EXCLUSIVE")
            source.execute("SELECT count(*) FROM sqlite_master").fetchone()     # its lock, held from here to the end
        except driver.OperationalError:
            raise InUse() from None
        for log in (path + "-wal", path + "-shm"):
            if os.path.exists(log):
                os.unlink(log)                  # the plaintext's own, folded in above: none may sit beside the copy
        before = _state(source)
        os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        source.execute("ATTACH DATABASE ? AS encrypted KEY ?", (target, f"x'{with_key.hex()}'"))
        source.execute("SELECT sqlcipher_export('encrypted')")
        source.execute(f"PRAGMA encrypted.user_version={int(before['version'])}")
        source.execute("DETACH DATABASE encrypted")
        copy = database.keyed(target, with_key=with_key)
        try:
            after = _state(copy)
        finally:
            copy.close()
        problems = _report(before, after, say, what)
        if problems:
            os.unlink(target)
            say(f"{what} was left as it was: {', '.join(problems)}.")
            return PROBLEM
        if replace:
            fd = os.open(target, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(target, path)            # while it is still held, so nothing writes to the plaintext meanwhile
            database.keep_private(path)
            say(f"{what} is encrypted now.")
        else:
            os.unlink(target)
        return OK
    finally:
        source.close()


def _state(c):
    """What the check compares: the schema's version, each table's rows, and the verdicts of SQLite's checks."""
    tables = [name for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE "
                                             "'sqlite_%' ORDER BY name")]
    state = {"version": c.execute("PRAGMA user_version").fetchone()[0],
             "rows": {t: c.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables},
             "integrity": [r for (r,) in c.execute("PRAGMA integrity_check")],
             "foreign keys": len(c.execute("PRAGMA foreign_key_check").fetchall()),
             "index": "none"}
    if "msg_fts" in tables:
        try:
            c.execute("INSERT INTO msg_fts(msg_fts) VALUES('integrity-check')")
            state["index"] = "ok"
        except driver.DatabaseError:
            state["index"] = "broken"
    return state


def _report(before, after, say, what):
    """Say what the copy holds against the database, and return what's wrong with it, if anything."""
    say(f"{what}, encrypted, against itself:")
    say("")
    width = max([len(t) for t in (*before["rows"], *after["rows"])] + [5])
    say(f"  {'table':<{width}}  {'before':>10}  {'after':>10}")
    for table in sorted(set(before["rows"]) | set(after["rows"])):
        b, a = before["rows"].get(table), after["rows"].get(table)
        say(f"  {table:<{width}}  {'-' if b is None else b:>10}  {'-' if a is None else a:>10}{'' if a == b else '  CHANGED'}")
    say("")
    integrity = "ok" if after["integrity"] == ["ok"] else f"{len(after['integrity'])} problem(s)"
    say(f"  integrity_check: {integrity}")
    say(f"  foreign_key_check: {after['foreign keys']} problem(s), {before['foreign keys']} before")
    say(f"  full-text index: {after['index']}")
    say(f"  schema version: {after['version']}, {before['version']} before")
    problems = []
    if after["rows"] != before["rows"]:
        problems.append("a table's rows differ")
    if after["integrity"] != ["ok"]:
        problems.append("the integrity check failed")
    if after["foreign keys"] != before["foreign keys"]:
        problems.append("the foreign keys differ")
    if after["index"] != before["index"]:
        problems.append("the full-text index differs")
    if after["version"] != before["version"]:
        problems.append("the schema's version differs")
    say(f"  the copy holds what the database does: {'yes' if not problems else 'no'}")
    say("")
    return problems
