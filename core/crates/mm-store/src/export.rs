//! What an export would hold, and as a download a snapshot of the database or the mail in a standard format
//! (`mailmind.platform.db.export`): read only, and no network. A download is a file in a folder of its own, made for
//! it and readable by its owner only, which goes when the `Download` does, however its answer ends. The snapshot,
//! encrypted as the database is, is made in the system's folder for temporary files; the archive, which holds the
//! mail as it is, in the data directory, beside the database.

// The mail as .eml files or an mbox a folder.
mod archive;
// The zip they are packed in.
mod zip;

use std::fs;
use std::io::BufWriter;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Moment, Outside};
use mm_kernel::when::civil;
use rusqlite::Connection;
use rusqlite::backup::Backup;
use serde_json::{Value, json};

use crate::db::Database;
pub use archive::Form;

const TRIPPED: Fault = Fault::new("Tripped");
const UNWRITABLE: Fault = Fault::new("ExportUnwritable");
const STORAGE: Fault = Fault::new("StorageError");
/// A message whose headers Python would have refused to write.
const REFUSED: Fault = Fault::new("ExportRefused");
/// How much of an archive is held before it goes to its file.
const CHUNK: usize = 262_144;

static FOLDERS: AtomicU64 = AtomicU64::new(0);

#[derive(Debug)]
pub struct Exports {
    database: Arc<Database>,
    outside: Arc<dyn Outside>,
    clock: Arc<dyn Clock>,
    /// Where a download's folder is made: the system's folder for temporary files.
    temporary: PathBuf,
}

/// A file to send, and the folder made for it, which goes when this does.
#[derive(Debug)]
pub struct Download {
    folder: PathBuf,
    pub file: PathBuf,
    /// What the file is called where it is saved.
    pub name: String,
    pub size: u64,
    pub content_type: &'static str,
    outside: Arc<dyn Outside>,
}

impl Drop for Download {
    fn drop(&mut self) {
        if self.outside.open(&self.folder).is_ok() {
            let _ = fs::remove_dir_all(&self.folder);
        }
    }
}

/// Whether a name is one [`Exports::folder`] gives: `mm-export-`, a process id, `-` and a count.
fn left_behind(name: &str) -> bool {
    let digits = |part: &str| !part.is_empty() && part.bytes().all(|b| b.is_ascii_digit());
    name.strip_prefix("mm-export-")
        .and_then(|rest| rest.split_once('-'))
        .is_some_and(|(pid, n)| digits(pid) && digits(n))
}

/// `%Y%m%dT%H%M%SZ` of a moment.
pub(crate) fn stamp(at: Moment) -> String {
    let seconds = at.whole_seconds();
    let (year, month, day) = civil(seconds.div_euclid(86_400));
    let of_day = seconds.rem_euclid(86_400);
    format!("{year:04}{month:02}{day:02}T{:02}{:02}{:02}Z", of_day / 3600, of_day % 3600 / 60, of_day % 60)
}

impl Exports {
    pub fn new(
        database: Arc<Database>,
        outside: Arc<dyn Outside>,
        clock: Arc<dyn Clock>,
        temporary: PathBuf,
    ) -> Exports {
        Exports { database, outside, clock, temporary }
    }

    /// What an export would hold, as a dry run: for each account and folder, how many messages and how many of
    /// them have their whole message stored.
    pub fn plan(&self) -> Result<Value, Fault> {
        let groups: Vec<(Value, Value, i64, Option<i64>)> = self.database.read(|c| {
            c.query_row("PRAGMA wal_checkpoint(TRUNCATE)", [], |_| Ok(()))?;
            c.prepare_cached(
                "SELECT account, folder, COUNT(*) n, SUM(body_fetched) bodies FROM messages \
                 GROUP BY account, folder ORDER BY account, folder",
            )?
            .query_map([], |r| {
                let text = |i| r.get::<_, Option<String>>(i).map(|t| t.map_or(Value::Null, Value::String));
                Ok((text(0)?, text(1)?, r.get(2)?, r.get(3)?))
            })?
            .collect()
        })?;
        let total: i64 = groups.iter().map(|g| g.2).sum();
        let bodies: i64 = groups.iter().map(|g| g.3.unwrap_or(0)).sum();
        let groups: Vec<Value> = groups
            .into_iter()
            .map(|(account, folder, n, b)| json!({"account": account, "folder": folder, "messages": n, "bodies": b.unwrap_or(0)}))
            .collect();
        Ok(json!({"ok": true, "total": total, "bodies": bodies, "groups": groups}))
    }

    /// Takes away the archive folders a process stopped mid-download left in the data directory: they hold mail, and
    /// nothing else would. Only a folder named as [`Exports::folder`] names one (`mm-export-<pid>-<n>`), and only one
    /// that is a folder itself, never what a link points to. A server runs this once, as it starts, before any
    /// download of its own; what it can't take away stays. The snapshot's folders are the system's to clear.
    pub fn sweep_left_behind(&self) {
        let within = self.database.folder();
        if self.outside.open(within).is_err() {
            return;
        }
        let Ok(entries) = fs::read_dir(within) else { return };
        for entry in entries.flatten() {
            let name = entry.file_name();
            let Some(name) = name.to_str() else { continue };
            if !left_behind(name) || !entry.file_type().is_ok_and(|kind| kind.is_dir()) {
                continue;
            }
            let folder = entry.path();
            if self.outside.open(&folder).is_ok() {
                let _ = fs::remove_dir_all(&folder);
            }
        }
    }

    /// A folder of its own for a download, in `within`, listable by its owner only.
    fn folder(&self, within: &Path, content_type: &'static str, name: String) -> Result<Download, Fault> {
        for _ in 0..64 {
            let n = FOLDERS.fetch_add(1, Ordering::Relaxed);
            let folder = within.join(format!("mm-export-{}-{n}", std::process::id()));
            self.outside.open(&folder).map_err(|_| TRIPPED)?;
            let mut builder = fs::DirBuilder::new();
            // A folder of its own: never one already there, nor the folders above it.
            builder.recursive(false);
            #[cfg(unix)]
            std::os::unix::fs::DirBuilderExt::mode(&mut builder, 0o700);
            match builder.create(&folder) {
                Ok(()) => {
                    let file = folder.join(&name);
                    return Ok(Download { folder, file, name, size: 0, content_type, outside: self.outside.clone() });
                }
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(_) => return Err(UNWRITABLE),
            }
        }
        Err(UNWRITABLE)
    }

    /// A new file in a download's folder, readable by its owner only.
    fn create(&self, path: &std::path::Path) -> Result<fs::File, Fault> {
        self.outside.open(path).map_err(|_| TRIPPED)?;
        let mut options = fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        std::os::unix::fs::OpenOptionsExt::mode(&mut options, 0o600);
        options.open(path).map_err(|_| UNWRITABLE)
    }

    /// A consistent snapshot of the database, its log checkpointed into it first, encrypted with the database's key:
    /// it opens on this computer, or wherever that key is given (MAILMIND_DB_KEY). SQLite's online backup copies it
    /// page for page into a file keyed as the database is, and writes nothing to the database itself.
    pub fn snapshot(&self) -> Result<Download, Fault> {
        let name = format!("mailmind-{}.db", stamp(self.clock.now()));
        let mut download = self.folder(&self.temporary, "application/octet-stream", name)?;
        // Made readable by its owner only, and closed again: SQLite opens it next.
        let _ = self.create(&download.file)?;
        let mut copy = Connection::open(&download.file).map_err(|_| UNWRITABLE)?;
        copy.execute_batch(&self.database.key().pragma()).map_err(|_| UNWRITABLE)?;
        self.database.read(|c| {
            c.query_row("PRAGMA wal_checkpoint(TRUNCATE)", [], |_| Ok(()))?;
            // Every page in one step, as Python's does, trying again a quarter second after the database was busy.
            Backup::new(c, &mut copy)?.run_to_completion(i32::MAX, Duration::from_millis(250), None)
        })?;
        drop(copy);
        download.size = fs::metadata(&download.file).map_err(|_| UNWRITABLE)?.len();
        Ok(download)
    }

    /// Every stored message as a zip any other mail program reads (`export.archive`): a .eml file each, under its
    /// account and folder, or an mbox file a folder. It is made in the data directory, beside the database, since
    /// it holds the mail as it is, and read from the database in one block, so it is the mail as it stood then.
    pub fn archive(&self, form: Form) -> Result<Download, Fault> {
        let now = self.clock.now();
        let name = format!("mailmind-{}-{}.zip", form.name(), stamp(now));
        let mut download = self.folder(self.database.folder(), "application/zip", name)?;
        let file = self.create(&download.file)?;
        let mut zip = zip::Zip::new(BufWriter::with_capacity(CHUNK, file), now, zip::PYTHONS);
        self.database.read(|c| Ok(archive::write_all(c, &mut zip, form)))?.map_err(|stop| match stop {
            archive::Stop::Storage => STORAGE,
            archive::Stop::Unwritable => UNWRITABLE,
            archive::Stop::Refused => REFUSED,
        })?;
        drop(zip.finish().map_err(|_| UNWRITABLE)?);
        download.size = fs::metadata(&download.file).map_err(|_| UNWRITABLE)?.len();
        Ok(download)
    }
}
