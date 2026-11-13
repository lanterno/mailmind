//! What an export would hold, and a snapshot of the database as a download (`mailmind.platform.db.export`): read
//! only, and no network. A download is a file in a folder of its own, made for it and readable by its owner only,
//! which goes when the `Download` does, however its answer ends.

use std::fs;
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};

use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Moment, Outside};
use mm_kernel::when::civil;
use rusqlite::params;
use serde_json::{Value, json};

use crate::db::Database;

const TRIPPED: Fault = Fault::new("Tripped");
const UNWRITABLE: Fault = Fault::new("ExportUnwritable");

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

/// `%Y%m%dT%H%M%SZ` of a moment.
fn stamp(at: Moment) -> String {
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

    /// A folder of its own for a download, listable by its owner only.
    fn folder(&self, content_type: &'static str, name: String) -> Result<Download, Fault> {
        for _ in 0..64 {
            let n = FOLDERS.fetch_add(1, Ordering::Relaxed);
            let folder = self.temporary.join(format!("mm-export-{}-{n}", std::process::id()));
            self.outside.open(&folder).map_err(|_| TRIPPED)?;
            let mut builder = fs::DirBuilder::new();
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
    /// it opens on this computer, or wherever that key is given (MAILMIND_DB_KEY). It copies every table and the
    /// schema's version, through SQLCipher's export, and writes nothing to the database itself.
    pub fn snapshot(&self) -> Result<Download, Fault> {
        let mut download =
            self.folder("application/octet-stream", format!("mailmind-{}.db", stamp(self.clock.now())))?;
        drop(self.create(&download.file)?);
        let path = download.file.to_str().ok_or(UNWRITABLE)?.to_owned();
        let key = self.database.key().raw();
        self.database.read(|c| {
            c.query_row("PRAGMA wal_checkpoint(TRUNCATE)", [], |_| Ok(()))?;
            let version: i64 = c.query_row("PRAGMA user_version", [], |r| r.get(0))?;
            c.execute("ATTACH DATABASE ?1 AS export KEY ?2", params![path, key.as_str()])?;
            let copied = c
                .query_row("SELECT sqlcipher_export('export')", [], |_| Ok(()))
                .and_then(|()| c.execute_batch(&format!("PRAGMA export.user_version = {version};")));
            let detached = c.execute_batch("DETACH DATABASE export");
            copied.and(detached)
        })?;
        download.size = fs::metadata(&download.file).map_err(|_| UNWRITABLE)?.len();
        Ok(download)
    }
}
