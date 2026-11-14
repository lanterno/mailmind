//! Copies of the database taken before its schema changes (`mailmind.platform.db.backup`).
//!
//! Each is a consistent snapshot, taken online with SQLite's backup API, so a write-ahead log not yet checkpointed is
//! in it, and encrypted with the database's key as the database is. It goes to `backups/` beside the database,
//! readable by its owner only, as `mailmind-v{version}-{UTC time}.db`; only the newest `KEEP` are kept. To roll back,
//! stop MailMind and copy one over the database (and delete the database's -wal and -shm files).

use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime};

use mm_kernel::seams::{Moment, Outside};
use rusqlite::Connection;
use rusqlite::backup::Backup;

use crate::DbKey;

/// How many copies are kept: the newest.
pub const KEEP: usize = 3;
/// The folder beside the database they go to.
pub const FOLDER: &str = "backups";

/// Why no copy was taken: the tripwire refused a file, or one couldn't be made or written.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct NoBackup;

/// Back up the database at `path`, which `c` is open on, at schema `version`, and prune the oldest copies. Returns
/// the new copy's path. A copy that couldn't be finished is taken away again, so none is left that looks whole.
pub(crate) fn take(
    c: &Connection,
    path: &Path,
    version: i64,
    key: &DbKey,
    outside: &dyn Outside,
    now: Moment,
) -> Result<PathBuf, NoBackup> {
    let folder = path.parent().unwrap_or(Path::new(".")).join(FOLDER);
    outside.open(&folder).map_err(|_| NoBackup)?;
    let mut builder = fs::DirBuilder::new();
    builder.recursive(true);
    #[cfg(unix)]
    std::os::unix::fs::DirBuilderExt::mode(&mut builder, 0o700);
    builder.create(&folder).map_err(|_| NoBackup)?;
    let dest = new_file(&folder, &format!("mailmind-v{version}-{}", crate::export::stamp(now)), outside)?;
    if copy(c, &dest, key).is_err() {
        let _ = fs::remove_file(&dest);
        return Err(NoBackup);
    }
    prune(&folder, outside);
    Ok(dest)
}

/// Every page of the database into `dest`, keyed as the database is, in one step, trying again a quarter second after
/// the database was busy.
fn copy(c: &Connection, dest: &Path, key: &DbKey) -> rusqlite::Result<()> {
    let mut copy = Connection::open(dest)?;
    copy.execute_batch(&key.pragma())?;
    Backup::new(c, &mut copy)?.run_to_completion(i32::MAX, Duration::from_millis(250), None)
}

/// A new, empty file for a copy, readable by its owner only: never another's file.
fn new_file(folder: &Path, stem: &str, outside: &dyn Outside) -> Result<PathBuf, NoBackup> {
    for n in 0..100 {
        let name = if n == 0 { format!("{stem}.db") } else { format!("{stem}-{n}.db") };
        let dest = folder.join(name);
        outside.open(&dest).map_err(|_| NoBackup)?;
        let mut options = fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        std::os::unix::fs::OpenOptionsExt::mode(&mut options, 0o600);
        match options.open(&dest) {
            Ok(_) => return Ok(dest),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(_) => return Err(NoBackup),
        }
    }
    Err(NoBackup)
}

/// Take away all but the newest `KEEP` copies in `folder`. One that can't be taken away is left.
fn prune(folder: &Path, outside: &dyn Outside) {
    let Ok(entries) = fs::read_dir(folder) else {
        return;
    };
    let mut copies: Vec<((SystemTime, u32), PathBuf)> = entries
        .filter_map(Result::ok)
        .filter_map(|entry| {
            let name = entry.file_name().into_string().ok()?;
            if !(name.starts_with("mailmind-v") && name.ends_with(".db")) {
                return None;
            }
            let modified = entry.metadata().ok()?.modified().ok()?;
            Some(((modified, numbered(&name)), entry.path()))
        })
        .collect();
    copies.sort();
    let gone = copies.len().saturating_sub(KEEP);
    for (_, old) in copies.into_iter().take(gone) {
        if outside.open(&old).is_ok() {
            let _ = fs::remove_file(&old);
        }
    }
}

/// The number `new_file` added to a copy named in the same second as another (`…Z-2.db`), 0 for none: oldest first,
/// by modification time and then by it.
fn numbered(name: &str) -> u32 {
    let Some((_, n)) = name.strip_suffix(".db").and_then(|stem| stem.rsplit_once("Z-")) else {
        return 0;
    };
    if n.bytes().all(|b| b.is_ascii_digit()) { n.parse().unwrap_or(0) } else { 0 }
}
