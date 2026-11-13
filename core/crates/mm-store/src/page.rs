//! The page's build as files: `index.html` and what is under `assets/`, in the folder `just build-web` writes
//! (`mailmind.platform.webroot`). This reads them, through `Outside`, and says when each last changed; what the
//! server makes of them (the token in the page, an asset's type, its ETag) is the server's.

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use mm_kernel::errors::Fault;
use mm_kernel::seams::Outside;

const TRIPPED: Fault = Fault::new("Tripped");
const UNREADABLE: Fault = Fault::new("UnreadableBuild");

/// When a file last changed, as far as a cache can tell: its size and its modification time in nanoseconds.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct Stamp {
    pub size: u64,
    pub modified_ns: i128,
}

/// One file of the build: where it is under the build's folder, `/` between its parts, and its stamp.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct BuildFile {
    pub relative: String,
    pub stamp: Stamp,
}

#[derive(Debug)]
pub struct PageFiles {
    dist: PathBuf,
    outside: Arc<dyn Outside>,
}

fn stamp(path: &Path) -> std::io::Result<Stamp> {
    let meta = fs::metadata(path)?;
    let modified_ns = meta
        .modified()?
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| i128::try_from(d.as_nanos()).unwrap_or(i128::MAX));
    Ok(Stamp { size: meta.len(), modified_ns })
}

/// Whether `path`, followed through any links, is a file.
fn is_file(path: &Path) -> bool {
    fs::metadata(path).is_ok_and(|m| m.is_file())
}

impl PageFiles {
    pub fn new(dist: PathBuf, outside: Arc<dyn Outside>) -> PageFiles {
        PageFiles { dist, outside }
    }

    fn index_path(&self) -> PathBuf {
        self.dist.join("index.html")
    }

    /// Whether anything has built the page.
    pub fn is_built(&self) -> bool {
        is_file(&self.index_path())
    }

    /// The page's text, as Python reads a text file: UTF-8, its line ends made `\n`. None when nothing built it.
    pub fn index(&self) -> Result<Option<String>, Fault> {
        let path = self.index_path();
        if !is_file(&path) {
            return Ok(None);
        }
        let bytes = self.read(&path)?;
        let text = String::from_utf8(bytes).map_err(|_| UNREADABLE)?;
        Ok(Some(text.replace("\r\n", "\n").replace('\r', "\n")))
    }

    fn read(&self, path: &Path) -> Result<Vec<u8>, Fault> {
        self.outside.open(path).map_err(|_| TRIPPED)?;
        fs::read(path).map_err(|_| UNREADABLE)
    }

    /// The build's files whose names `served` takes (`index.html` always), in the order Python sorts their paths.
    /// Links to folders aren't followed, as `os.walk` follows none.
    pub fn files(&self, served: &dyn Fn(&str) -> bool) -> Result<Vec<BuildFile>, Fault> {
        let mut found = vec![self.index_path()];
        walk(&self.dist.join("assets"), served, &mut found);
        let mut files = Vec::new();
        for path in found.into_iter().filter(|p| is_file(p)) {
            let Ok(relative) = path.strip_prefix(&self.dist) else { continue };
            let Some(relative) = relative.to_str() else { continue };
            files.push(BuildFile { relative: relative.to_owned(), stamp: stamp(&path).map_err(|_| UNREADABLE)? });
        }
        files.sort_by(|a, b| a.relative.cmp(&b.relative));
        Ok(files)
    }

    /// A file of the build by where it is under the build's folder, as `files` names it.
    pub fn file(&self, relative: &str) -> Result<Vec<u8>, Fault> {
        self.read(&self.dist.join(relative))
    }

    /// The file at `parts` under `assets/`, and its stamp: None unless it is a file, and inside the build's folder
    /// once every link is followed.
    pub fn asset(&self, parts: &[&str]) -> Result<Option<(PathBuf, Stamp)>, Fault> {
        let mut path = self.dist.join("assets");
        path.extend(parts);
        if !is_file(&path) {
            return Ok(None);
        }
        let (Ok(real), Ok(root)) = (fs::canonicalize(&path), fs::canonicalize(&self.dist)) else {
            return Ok(None);
        };
        if real == root || !real.starts_with(&root) {
            return Ok(None);
        }
        Ok(Some((path.clone(), stamp(&path).map_err(|_| UNREADABLE)?)))
    }

    /// What an asset `asset` found holds.
    pub fn asset_body(&self, path: &Path) -> Result<Vec<u8>, Fault> {
        self.read(path)
    }
}

/// Every file under `folder` whose name `served` takes, into `found`.
fn walk(folder: &Path, served: &dyn Fn(&str) -> bool, found: &mut Vec<PathBuf>) {
    let Ok(entries) = fs::read_dir(folder) else { return };
    let mut entries: Vec<_> = entries.filter_map(Result::ok).collect();
    entries.sort_by_key(fs::DirEntry::file_name);
    for entry in entries {
        let path = entry.path();
        let Ok(kind) = entry.file_type() else { continue };
        if kind.is_dir() {
            walk(&path, served, found);
        } else if kind.is_symlink() && fs::metadata(&path).is_ok_and(|m| m.is_dir()) {
            continue;
        } else if entry.file_name().to_str().is_some_and(served) {
            found.push(path);
        }
    }
}
