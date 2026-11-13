//! The app's page and its files, as `just build-web` wrote them (`mailmind.platform.webroot`).
//!
//! The app exists only as built: with nothing built there is no page to serve. A build names each file by its
//! content, so a file is served for good, and a new build is a new edition. `PageFiles` (mm-store) reads the files;
//! this says what is served of them.

use std::collections::HashMap;
use std::sync::Mutex;

use flate2::Compression;
use flate2::write::GzEncoder;
use mm_kernel::errors::Fault;
use mm_store::PageFiles;
use mm_store::page::{BuildFile, Stamp};
use sha2::{Digest, Sha256};

pub const PREFIX: &str = "/assets/";
pub const FOREVER: &str = "public, max-age=31536000, immutable";

/// The types of the files served, by extension.
const TYPES: [(&str, &str); 3] =
    [("css", "text/css; charset=utf-8"), ("js", "text/javascript; charset=utf-8"), ("woff2", "font/woff2")];
/// What is worth compressing.
const COMPRESSED: [&str; 2] = ["css", "js"];
const POISONED: Fault = Fault::new("PoisonedLock");

/// One of the page's files, as served.
#[derive(Clone, Debug)]
pub struct Asset {
    pub body: Vec<u8>,
    /// The body compressed, for a type worth compressing.
    pub gzipped: Option<Vec<u8>>,
    pub content_type: &'static str,
    pub etag: String,
}

#[derive(Debug)]
pub struct Page {
    files: PageFiles,
    /// What the files were, and the build id they make.
    build: Mutex<Option<(Vec<BuildFile>, String)>>,
    /// Each asset read, by where it is, with its stamp then.
    read: Mutex<HashMap<String, (Stamp, Asset)>>,
}

fn hex(digest: &[u8]) -> String {
    digest.iter().map(|b| format!("{b:02x}")).collect()
}

/// A file name's extension as `os.path.splitext` finds it: after the last dot, unless only dots come before it.
fn extension(name: &str) -> Option<&str> {
    let (stem, ext) = name.rsplit_once('.')?;
    (!stem.chars().all(|c| c == '.')).then_some(ext)
}

fn content_type(ext: &str) -> Option<&'static str> {
    TYPES.iter().find(|(e, _)| *e == ext).map(|(_, t)| *t)
}

impl Page {
    pub fn new(files: PageFiles) -> Page {
        Page { files, build: Mutex::new(None), read: Mutex::new(HashMap::new()) }
    }

    /// A name for what is being served now, which a tab compares with the one it loaded under. It goes by the files'
    /// content, so a build that made the same files changes nothing.
    fn build(&self) -> Result<String, Fault> {
        let files = self.files.files(&|name| extension(name).and_then(content_type).is_some())?;
        let mut kept = self.build.lock().map_err(|_| POISONED)?;
        if let Some((seen, id)) = kept.as_ref()
            && *seen == files
        {
            return Ok(id.clone());
        }
        let mut digest = Sha256::new();
        for file in &files {
            digest.update(file.relative.as_bytes());
            digest.update(b"\0");
            digest.update(self.files.file(&file.relative)?);
        }
        let id = hex(&digest.finalize())[..16].to_owned();
        *kept = Some((files, id.clone()));
        Ok(id)
    }

    /// What a tab compares with what it loaded under: the files, and the token it was given, which a restart
    /// changes. Only a hash of the token goes out.
    pub fn edition(&self, csrf: &str) -> Result<String, Fault> {
        Ok(format!("{}-{}", self.build()?, &hex(&Sha256::digest(csrf.as_bytes()))[..8]))
    }

    /// The app's page holding this run's token, or None when nothing has built it. `place` is the route a place
    /// holds, as its JSON text, which the page reads where it is from (`<meta name="mailmind-place">`, empty for none):
    /// escaped for an attribute.
    pub fn page(&self, csrf: &str, place: Option<&str>) -> Result<Option<String>, Fault> {
        let Some(index) = self.files.index()? else {
            return Ok(None);
        };
        Ok(Some(
            index
                .replace("__CSRF_TOKEN__", csrf)
                .replace("__BUILD__", &self.edition(csrf)?)
                .replace("__PLACE__", &escape(place.unwrap_or_default())),
        ))
    }

    /// Whether anything has built the page.
    pub fn is_built(&self) -> bool {
        self.files.is_built()
    }

    /// The asset at a URL path under PREFIX, or None for anything that isn't one of the page's files.
    pub fn asset(&self, url_path: &str) -> Result<Option<Asset>, Fault> {
        let relative = url_path.strip_prefix(PREFIX).unwrap_or(url_path);
        let file_name = relative.rsplit('/').next().unwrap_or(relative);
        let Some(ext) = extension(file_name) else { return Ok(None) };
        let Some(content_type) = content_type(ext) else { return Ok(None) };
        let parts: Vec<&str> = relative.split('/').collect();
        if relative.contains('\\') || parts.iter().any(|p| p.is_empty() || p.starts_with('.')) {
            return Ok(None);
        }
        let Some((path, stamp)) = self.files.asset(&parts)? else {
            return Ok(None);
        };
        if let Some((seen, asset)) = self.read.lock().map_err(|_| POISONED)?.get(relative)
            && *seen == stamp
        {
            return Ok(Some(asset.clone()));
        }
        let body = self.files.asset_body(&path)?;
        let gzipped = if COMPRESSED.contains(&ext) { Some(gzip(&body)?) } else { None };
        let etag = format!("W/\"{}\"", &hex(&Sha256::digest(&body))[..24]);
        let asset = Asset { body, gzipped, content_type, etag };
        self.read.lock().map_err(|_| POISONED)?.insert(relative.to_owned(), (stamp, asset.clone()));
        Ok(Some(asset))
    }
}

/// `html.escape(text, quote=True)`: the five characters an attribute can't hold as they are.
fn escape(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&#x27;"),
            _ => out.push(c),
        }
    }
    out
}

/// `gzip.compress(body, 6, mtime=0)`: the same header, though not always the same deflate bytes.
fn gzip(body: &[u8]) -> Result<Vec<u8>, Fault> {
    use std::io::Write;
    let mut encoder = GzEncoder::new(Vec::new(), Compression::new(6));
    encoder.write_all(body).map_err(|_| Fault::new("CompressionFailed"))?;
    encoder.finish().map_err(|_| Fault::new("CompressionFailed"))
}
