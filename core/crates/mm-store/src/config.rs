//! `mailmind.config.json` (`mm_accounts::ports::ConfigFile`), parsed only when it has changed since the last parse:
//! another inode (a save replaces the file), another size or another modification time (an edit in place). Each
//! read is a copy of the caller's own (`mailmind.accounts.adapters.config_file`).
//!
//! A write replaces the file atomically: the new text goes to a temporary file beside it, flushed to the disk and
//! made readable by its owner only, then renamed over the old one, so a crash never leaves a partial or
//! world-readable file. The text is what Python's `json.dumps(raw, indent=2, ensure_ascii=False)` writes, keys in
//! the order they came.

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use mm_accounts::ports;
use mm_kernel::errors::Fault;
use mm_kernel::seams::Outside;
use serde_json::{Map, Value};

const TRIPPED: Fault = Fault::new("Tripped");
const UNREADABLE: Fault = Fault::new("UnreadableConfig");
const UNWRITABLE: Fault = Fault::new("UnwritableConfig");
const SHAPE: Fault = Fault::new("BadConfigShape");

/// The file's identity and what it held when it was last parsed.
#[derive(Debug, Clone, PartialEq, Eq)]
struct Seen {
    inode: u64,
    size: u64,
    modified: Option<std::time::SystemTime>,
}

#[derive(Debug)]
pub struct ConfigFile {
    path: PathBuf,
    outside: Arc<dyn Outside>,
    parsed: Mutex<Option<(Seen, Map<String, Value>)>>,
}

static TEMPORARY: AtomicU64 = AtomicU64::new(0);

impl ConfigFile {
    pub fn new(path: PathBuf, outside: Arc<dyn Outside>) -> ConfigFile {
        ConfigFile { path, outside, parsed: Mutex::new(None) }
    }

    fn seen(path: &Path) -> std::io::Result<Seen> {
        let meta = fs::metadata(path)?;
        #[cfg(unix)]
        let inode = std::os::unix::fs::MetadataExt::ino(&meta);
        #[cfg(not(unix))]
        let inode = 0;
        Ok(Seen { inode, size: meta.len(), modified: meta.modified().ok() })
    }
}

impl ports::ConfigFile for ConfigFile {
    fn read(&self) -> Result<Map<String, Value>, Fault> {
        self.outside.open(&self.path).map_err(|_| TRIPPED)?;
        let seen = match ConfigFile::seen(&self.path) {
            Ok(seen) => seen,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                let mut empty = Map::new();
                empty.insert("accounts".into(), Value::Array(Vec::new()));
                return Ok(empty);
            }
            Err(_) => return Err(UNREADABLE),
        };
        let mut parsed = self.parsed.lock().map_err(|_| UNREADABLE)?;
        if let Some((kept, raw)) = parsed.as_ref()
            && *kept == seen
        {
            return Ok(raw.clone());
        }
        let text = fs::read(&self.path).map_err(|_| UNREADABLE)?;
        let Ok(Value::Object(raw)) = serde_json::from_slice::<Value>(&text) else {
            return Err(UNREADABLE);
        };
        *parsed = Some((seen, raw.clone()));
        Ok(raw)
    }

    fn write(&self, raw: &Map<String, Value>) -> Result<(), Fault> {
        if !raw.get("accounts").is_some_and(Value::is_array) {
            return Err(SHAPE);
        }
        let text = serde_json::to_string_pretty(raw).map_err(|_| SHAPE)?;
        let folder = self.path.parent().ok_or(UNWRITABLE)?;
        let n = TEMPORARY.fetch_add(1, Ordering::Relaxed);
        let temporary = folder.join(format!(".cfg-{}-{n}.tmp", std::process::id()));
        self.outside.open(&temporary).map_err(|_| TRIPPED)?;
        self.outside.open(&self.path).map_err(|_| TRIPPED)?;
        let written = (|| -> std::io::Result<()> {
            let mut options = fs::OpenOptions::new();
            options.write(true).create_new(true);
            #[cfg(unix)]
            std::os::unix::fs::OpenOptionsExt::mode(&mut options, 0o600);
            let mut file = options.open(&temporary)?;
            file.write_all(text.as_bytes())?;
            file.flush()?;
            file.sync_all()?;
            #[cfg(unix)]
            fs::set_permissions(&temporary, std::os::unix::fs::PermissionsExt::from_mode(0o600))?;
            fs::rename(&temporary, &self.path)
        })();
        if written.is_err() {
            let _ = fs::remove_file(&temporary);
            return Err(UNWRITABLE);
        }
        Ok(())
    }
}
