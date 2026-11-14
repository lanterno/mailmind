//! What the next start's migrations would do to a database, tried on a copy (`mailmind.platform.db.check`), as
//! `mailmind-server db check-upgrade` runs it.
//!
//! It copies the database, read-only, to a folder of its own beside it (so the mail stays in the data directory),
//! migrates the copy the way a start would, and runs SQLite's integrity, foreign-key and full-text-index checks on it.
//! It says only table names, row counts and the checks' verdicts, never a row's contents nor where the database is,
//! so its report can be shared. The database itself is never written, and the folder goes when the check ends.
//!
//! A row that points at a row that's gone (a body or summary whose message was deleted while an older MailMind left
//! foreign keys off) is the database's, not the upgrade's: the foreign-key check counts what was there before the
//! migrations too, and fails only on what they add.
//!
//! What a pending migration declares (`Migration`) is no loss: a table it retires, a change in the count of one it
//! rebuilds, the growth of one it fills, and exactly the rows it trims, counted before it runs (one row more is a
//! loss). The full-text index's own tables (`msg_fts_data` and the like) aren't listed.

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Duration;

use mm_kernel::seams::{Clock, Outside};
use rusqlite::backup::Backup;
use rusqlite::{Connection, OpenFlags};

use crate::DbKey;
use crate::db::Database;
use crate::migrate::{self, BASE, MIGRATIONS, Migration, NotReady};

/// What the check found, and the exit status it is: every check passed and every table kept its rows (0); something
/// didn't (1); or there was nothing it could check (2).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Verdict {
    Passed,
    Problem,
    CannotRun,
}

impl Verdict {
    /// The exit status a program says it with, as `check.OK`, `PROBLEM` and `CANNOT_RUN` are.
    pub fn code(self) -> u8 {
        match self {
            Verdict::Passed => 0,
            Verdict::Problem => 1,
            Verdict::CannotRun => 2,
        }
    }
}

/// Check what the core's migrations would do to `database`, and report each line through `say`. `clock` names the
/// copy's own backup, which goes with the copy.
pub fn check_upgrade(database: &Database, clock: &dyn Clock, say: &mut dyn FnMut(&str)) -> Verdict {
    check_upgrade_with(database, clock, MIGRATIONS, say)
}

/// `check_upgrade`, with `migrations` in place of the core's own (a test's).
pub fn check_upgrade_with(
    database: &Database,
    clock: &dyn Clock,
    migrations: &[Migration],
    say: &mut dyn FnMut(&str),
) -> Verdict {
    let (path, key, outside) = (database.path(), database.key(), database.outside());
    if outside.open(path).is_err() || !path.is_file() {
        say("No database to check.");
        return Verdict::CannotRun;
    }
    let Some(folder) = Folder::beside(path, outside) else {
        say("No folder for the copy could be made beside the database.");
        return Verdict::CannotRun;
    };
    let copy = folder.path.join("upgrade-copy.db");
    match copy_read_only(path, &copy, key, outside) {
        Ok(()) => {}
        Err(Copying::KeyDoesNotFit) => {
            say("The database key doesn't open the database.");
            return Verdict::CannotRun;
        }
        Err(Copying::Failed) => {
            say("The database couldn't be copied.");
            return Verdict::CannotRun;
        }
    }
    match tried(&copy, key, outside, clock, migrations, say) {
        Ok(verdict) => verdict,
        Err(Unread) => {
            say("The copy of the database couldn't be read.");
            Verdict::CannotRun
        }
    }
}

/// The copy couldn't be read: SQLite's words name tables and columns, and a value could follow, so none are kept.
struct Unread;

impl From<rusqlite::Error> for Unread {
    fn from(_: rusqlite::Error) -> Unread {
        Unread
    }
}

/// Count the copy, migrate it, count it again, check it, and say what changed.
fn tried(
    copy: &Path,
    key: &DbKey,
    outside: &dyn Outside,
    clock: &dyn Clock,
    migrations: &[Migration],
    say: &mut dyn FnMut(&str),
) -> Result<Verdict, Unread> {
    let (before, from, keys_before, to_go) = {
        let c = keyed(copy, key)?;
        let before = counts(&c)?;
        let from = version(&c)?;
        let to_go = rows_to_go(&c, migrate::pending(migrations, from), &before)?;
        (before, from, key_problems(&c)?, to_go)
    };
    let newest = migrations.last().map_or(BASE, |last| last.version);
    match migrate::ready_at(copy, key, outside, clock, migrations) {
        Ok(_) => {}
        Err(NotReady::Older(version)) => {
            say(&format!(
                "The database is at version {version}, older than the version {BASE} the core upgrades from: the \
                 Python app's `db check-upgrade` checks its upgrade."
            ));
            return Ok(Verdict::CannotRun);
        }
        Err(NotReady::Newer(version)) => {
            say(&format!("The database is at version {version}, newer than the version {newest} this core reads."));
            return Ok(Verdict::CannotRun);
        }
        Err(NotReady::Failed(version)) => {
            say(&format!(
                "A copy of the database, migrated from version {from}: the migration to version {version} failed on \
                 it, so an upgrade would stop there and leave the database as it was before that migration."
            ));
            return Ok(Verdict::Problem);
        }
        Err(_) => return Err(Unread),
    }
    let c = keyed(copy, key)?;
    let after = counts(&c)?;
    let to = version(&c)?;
    let checks = checks(&c, &keys_before)?;
    drop(c);

    let pending: Vec<&Migration> = migrate::pending(migrations, from).collect();
    let declares =
        |table: &str, what: fn(&Migration) -> &'static [&'static str]| pending.iter().any(|m| what(m).contains(&table));
    say(&format!("A copy of the database, migrated from version {from} to {to}:"));
    say("");
    let tables: BTreeSet<&String> = before.keys().chain(after.keys()).collect();
    let width = tables.iter().map(|t| t.chars().count()).max().unwrap_or(0).max(5);
    say(&format!("  {:<width$}  {:>10}  {:>10}", "table", "before", "after"));
    let mut changed: Vec<&str> = Vec::new();
    for table in tables {
        let (b, a) = (before.get(table).copied(), after.get(table).copied());
        let retired = a.is_none() && declares(table, |m| m.retires);
        let rebuilt = declares(table, |m| m.rebuilds);
        let filled = matches!((b, a), (Some(b), Some(a)) if a > b) && declares(table, |m| m.fills);
        let trimmed = matches!((b, a), (Some(b), Some(a)) if a != b && a == b - to_go.get(table).copied().unwrap_or(0));
        let note = if a == b {
            ""
        } else if b.is_none() {
            "  new"
        } else if retired {
            "  retired"
        } else if rebuilt {
            "  rebuilt"
        } else if filled {
            "  carried over"
        } else if trimmed {
            "  trimmed"
        } else {
            "  CHANGED"
        };
        if b.is_some() && a != b && !retired && !rebuilt && !filled && !trimmed {
            changed.push(table);
        }
        say(&format!("  {table:<width$}  {:>10}  {:>10}{note}", count(b), count(a)));
    }
    say("");
    for (name, verdict, _) in &checks {
        say(&format!("  {name}: {verdict}"));
    }
    let kept = if changed.is_empty() { "yes".to_owned() } else { format!("no ({})", changed.join(", ")) };
    say(&format!("  every table kept its rows: {kept}"));
    let passed = changed.is_empty() && checks.iter().all(|(_, _, passed)| *passed);
    Ok(if passed { Verdict::Passed } else { Verdict::Problem })
}

fn count(n: Option<i64>) -> String {
    n.map_or_else(|| "-".to_owned(), |n| n.to_string())
}

/// A folder of its own beside the database, listable by its owner only, which goes when this does.
struct Folder<'a> {
    path: PathBuf,
    outside: &'a dyn Outside,
}

impl<'a> Folder<'a> {
    fn beside(database: &Path, outside: &'a dyn Outside) -> Option<Folder<'a>> {
        let parent = database.parent().unwrap_or(Path::new(""));
        for n in 0..64 {
            let path = parent.join(format!(".mailmind-check-{}-{n}", std::process::id()));
            outside.open(&path).ok()?;
            let mut builder = fs::DirBuilder::new();
            #[cfg(unix)]
            std::os::unix::fs::DirBuilderExt::mode(&mut builder, 0o700);
            match builder.create(&path) {
                Ok(()) => return Some(Folder { path, outside }),
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(_) => return None,
            }
        }
        None
    }
}

impl Drop for Folder<'_> {
    fn drop(&mut self) {
        if self.outside.open(&self.path).is_ok() {
            let _ = fs::remove_dir_all(&self.path);
        }
    }
}

enum Copying {
    KeyDoesNotFit,
    Failed,
}

/// Every page of the database at `path`, opened so that nothing can write through it, into `dest`, keyed as the
/// database is.
fn copy_read_only(path: &Path, dest: &Path, key: &DbKey, outside: &dyn Outside) -> Result<(), Copying> {
    let flags = OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX;
    let source = Connection::open_with_flags(path, flags).map_err(|_| Copying::Failed)?;
    source.execute_batch(&key.pragma()).map_err(|_| Copying::Failed)?;
    match source.query_row("SELECT count(*) FROM sqlite_master", [], |r| r.get::<_, i64>(0)) {
        Ok(_) => {}
        Err(rusqlite::Error::SqliteFailure(e, _)) if e.code == rusqlite::ErrorCode::NotADatabase => {
            return Err(Copying::KeyDoesNotFit);
        }
        Err(_) => return Err(Copying::Failed),
    }
    outside.open(dest).map_err(|_| Copying::Failed)?;
    let mut target = Connection::open(dest).map_err(|_| Copying::Failed)?;
    target.execute_batch(&key.pragma()).map_err(|_| Copying::Failed)?;
    let copied = Backup::new(&source, &mut target)
        .and_then(|backup| backup.run_to_completion(i32::MAX, Duration::from_millis(250), None));
    copied.map_err(|_| Copying::Failed)
}

/// A bare keyed connection to the copy, as `database.keyed` opens one.
fn keyed(path: &Path, key: &DbKey) -> rusqlite::Result<Connection> {
    let c = Connection::open(path)?;
    c.execute_batch(&key.pragma())?;
    Ok(c)
}

fn version(c: &Connection) -> rusqlite::Result<i64> {
    c.query_row("PRAGMA user_version", [], |r| r.get(0))
}

/// Each table's rows, by name: SQLite's own tables and the full-text index's left out.
fn counts(c: &Connection) -> rusqlite::Result<BTreeMap<String, i64>> {
    let names: Vec<String> = c
        .prepare(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' \
             AND name NOT GLOB 'msg_fts_*' ORDER BY name",
        )?
        .query_map([], |r| r.get(0))?
        .collect::<rusqlite::Result<_>>()?;
    names
        .into_iter()
        .map(|name| {
            let n =
                c.query_row(&format!("SELECT count(*) FROM \"{}\"", name.replace('"', "\"\"")), [], |r| r.get(0))?;
            Ok((name, n))
        })
        .collect()
}

/// What the migrations still to run take from a table on purpose, by table.
fn rows_to_go<'m>(
    c: &Connection,
    pending: impl Iterator<Item = &'m Migration>,
    tables: &BTreeMap<String, i64>,
) -> rusqlite::Result<BTreeMap<String, i64>> {
    let mut to_go: BTreeMap<String, i64> = BTreeMap::new();
    for (table, how) in pending.flat_map(|m| m.trims.iter()) {
        if tables.contains_key(*table) {
            let n: i64 = c.query_row(how, [], |r| r.get(0))?;
            *to_go.entry((*table).to_owned()).or_default() += n;
        }
    }
    Ok(to_go)
}

/// How many rows point at a row that's gone, by (the table, the table it points at).
fn key_problems(c: &Connection) -> rusqlite::Result<BTreeMap<(String, String), i64>> {
    let mut found: BTreeMap<(String, String), i64> = BTreeMap::new();
    let mut statement = c.prepare("PRAGMA foreign_key_check")?;
    let mut rows = statement.query([])?;
    while let Some(row) = rows.next()? {
        *found.entry((row.get(0)?, row.get(2)?)).or_default() += 1;
    }
    Ok(found)
}

/// `table n, …`, summed over the tables each points at.
fn by_table(problems: &BTreeMap<(String, String), i64>) -> String {
    let mut counts: BTreeMap<&str, i64> = BTreeMap::new();
    for ((table, _), n) in problems {
        *counts.entry(table.as_str()).or_default() += n;
    }
    counts.iter().map(|(table, n)| format!("{table} {n}")).collect::<Vec<_>>().join(", ")
}

/// (check, verdict, passed): each verdict "ok", or the number of problems found, and for foreign keys the tables
/// they're in, never what they are about. `keys_before` is what `key_problems` found before the migrations.
fn checks(
    c: &Connection,
    keys_before: &BTreeMap<(String, String), i64>,
) -> rusqlite::Result<Vec<(&'static str, String, bool)>> {
    let mut out = Vec::new();
    let integrity: Vec<String> =
        c.prepare("PRAGMA integrity_check")?.query_map([], |r| r.get(0))?.collect::<rusqlite::Result<_>>()?;
    let whole = integrity == ["ok"];
    out.push((
        "integrity_check",
        if whole { "ok".to_owned() } else { format!("{} problem(s)", integrity.len()) },
        whole,
    ));

    let keys = key_problems(c)?;
    let added: BTreeMap<(String, String), i64> = keys
        .iter()
        .filter_map(|(at, n)| {
            let more = n - keys_before.get(at).copied().unwrap_or(0);
            (more > 0).then(|| (at.clone(), more))
        })
        .collect();
    let total: i64 = keys.values().sum();
    let verdict = if total == 0 {
        "ok".to_owned()
    } else if added.is_empty() {
        format!("{total} problem(s), all there before the upgrade ({})", by_table(&keys))
    } else {
        format!("{total} problem(s), {} of them new ({})", added.values().sum::<i64>(), by_table(&added))
    };
    out.push(("foreign_key_check", verdict, added.is_empty()));

    let indexed: i64 = c.query_row("SELECT count(*) FROM sqlite_master WHERE name = 'msg_fts'", [], |r| r.get(0))?;
    if indexed > 0 {
        let checked = c.execute("INSERT INTO msg_fts(msg_fts) VALUES('integrity-check')", []).and_then(|_| {
            c.query_row("SELECT count(*) FROM messages WHERE pk NOT IN (SELECT rowid FROM msg_fts)", [], |r| {
                r.get::<_, i64>(0)
            })
        });
        out.push(match checked {
            Ok(0) => ("full-text index", "ok".to_owned(), true),
            Ok(missing) => ("full-text index", format!("{missing} message(s) not in it"), false),
            Err(_) => ("full-text index", "1 problem(s)".to_owned(), false),
        });
    }
    Ok(out)
}
