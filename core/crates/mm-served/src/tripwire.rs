//! The tripwire (tests/support/guard.py's, for the Rust server): no test may touch the owner's mail data, reach past
//! this machine, or start a credential helper or the claude CLI.
//!
//! The app asks it (`Outside`) before it opens a file, reaches a host or starts a program. Each refusal is an error
//! the app sees, so the act never happens, and is kept, so a caller that swallows the error still fails the test:
//! `check` reports it.
//!
//! A file is refused when it is one of the owner's data files (`PRIVATE`) in the repo root or a folder
//! MAILMIND_TEST_GUARDED names, as guard.py refuses it; and any other file under those folders is refused too, but
//! in the data directory the test made and the page's build.

use std::net::IpAddr;
use std::path::{Component, Path, PathBuf};
use std::sync::Mutex;

use mm_kernel::seams::{Outside, Tripped};

/// The owner's data in a data directory (CLAUDE.md, "Nothing personal is committed"), and the temporary files
/// written beside it, as guard.py lists them.
const PRIVATE: [&str; 14] = [
    "mailmind.db",
    "mailmind.db-*",
    "mailmind.config.json*",
    "mailmind_rules.json",
    "mailmind_views.json",
    "mailmind_snippets.json",
    "*.jsonl",
    "*.sqlite",
    "*.db-journal",
    ".cfg-*.tmp",
    ".views-*.tmp",
    ".snippets-*.tmp",
    "mailmind.log",
    "mailmind.log.*",
];
/// A real AI call, a 1Password read and a Keychain read.
const PROGRAMS: [&str; 3] = ["claude", "op", "security"];

#[derive(Debug)]
pub struct Tripwire {
    /// Where the owner's data may live: the repo root and each guarded folder, as `realpath` gives them, lowered.
    guarded: Vec<String>,
    /// What may be opened under them all the same: the test's data directory and the page's build.
    allowed: Vec<PathBuf>,
    refused: Mutex<Vec<String>>,
}

/// `os.path.abspath`: from the current directory, `.` and `..` worked out from the text alone.
fn absolute(path: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for part in std::path::absolute(path).unwrap_or_else(|_| path.to_path_buf()).components() {
        match part {
            Component::ParentDir => {
                out.pop();
            }
            Component::CurDir => {}
            other => out.push(other),
        }
    }
    out
}

/// `os.path.realpath(os.path.abspath(path))`: links followed as far as the path exists.
fn real(path: &Path) -> PathBuf {
    let path = absolute(path);
    if let Ok(found) = std::fs::canonicalize(&path) {
        return found;
    }
    match (path.parent(), path.file_name()) {
        (Some(parent), Some(name)) => real(parent).join(name),
        _ => path,
    }
}

/// `fnmatch.fnmatchcase` for the patterns above: `*` is any run of characters, and nothing else is special.
fn matches(pattern: &str, name: &str) -> bool {
    match pattern.split_once('*') {
        None => pattern == name,
        Some((head, tail)) => {
            let Some(rest) = name.strip_prefix(head) else { return false };
            if !tail.contains('*') {
                return rest.len() >= tail.len() && rest.ends_with(tail);
            }
            (0..=rest.len()).filter(|&i| rest.is_char_boundary(i)).any(|i| matches(tail, &rest[i..]))
        }
    }
}

/// Whether `host` is off this machine: anything but `localhost`, an empty host, or a loopback address.
fn remote(host: &str) -> bool {
    let host = mm_kernel::text::strip(host).to_lowercase();
    if host.is_empty() || host == "localhost" {
        return false;
    }
    let address = host.split('%').next().unwrap_or_default();
    match address.parse::<IpAddr>() {
        Ok(IpAddr::V4(v4)) => !v4.is_loopback(),
        Ok(IpAddr::V6(v6)) => !(v6.is_loopback() || v6.to_ipv4_mapped().is_some_and(|v4| v4.is_loopback())),
        Err(_) => true,
    }
}

impl Tripwire {
    /// Guarding `root` and each of `guarded`, and letting through what is in `allowed`.
    pub fn new(root: &Path, guarded: &[PathBuf], allowed: &[PathBuf]) -> Tripwire {
        let lowered = |p: &Path| real(p).to_string_lossy().to_lowercase();
        Tripwire {
            guarded: std::iter::once(root).chain(guarded.iter().map(PathBuf::as_path)).map(lowered).collect(),
            allowed: allowed.iter().map(|p| real(p)).collect(),
            refused: Mutex::new(Vec::new()),
        }
    }

    /// What it refused since this was last asked.
    pub fn take(&self) -> Vec<String> {
        self.refused.lock().map(|mut r| std::mem::take(&mut *r)).unwrap_or_default()
    }

    fn refuse(&self, what: String) -> Result<(), Tripped> {
        if let Ok(mut refused) = self.refused.lock() {
            refused.push(what.clone());
        }
        Err(Tripped(what))
    }

    fn private(&self, found: &Path) -> bool {
        let (Some(folder), Some(name)) = (found.parent(), found.file_name()) else { return false };
        let folder = folder.to_string_lossy().to_lowercase();
        let name = name.to_string_lossy().to_lowercase();
        self.guarded.contains(&folder) && PRIVATE.iter().any(|pattern| matches(pattern, &name))
    }

    fn under_guard(&self, found: &Path) -> bool {
        let lowered = found.to_string_lossy().to_lowercase();
        let inside = |folder: &str| lowered.strip_prefix(folder).is_some_and(|rest| rest.starts_with('/'));
        self.guarded.iter().any(|folder| inside(folder)) && !self.allowed.iter().any(|a| found.starts_with(a))
    }
}

impl Outside for Tripwire {
    fn open(&self, path: &Path) -> Result<(), Tripped> {
        let found = real(path);
        if self.private(&found) || self.under_guard(&found) {
            return self.refuse(format!("open {:?}", path.display().to_string()));
        }
        Ok(())
    }

    fn reach(&self, host: &str) -> Result<(), Tripped> {
        if !remote(host) {
            return Ok(());
        }
        let what = if host.parse::<IpAddr>().is_ok() { "connect to" } else { "DNS lookup of" };
        self.refuse(format!("{what} {host:?}"))
    }

    fn run(&self, program: &str) -> Result<(), Tripped> {
        let name = Path::new(program).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        if PROGRAMS.contains(&name.as_str()) {
            return self.refuse(format!("start {name}"));
        }
        Ok(())
    }
}
