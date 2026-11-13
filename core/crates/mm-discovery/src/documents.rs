//! The Document Hub (`discovery.application.documents` and `discovery.domain.documents`): every real attachment
//! across the accounts, found by kind and by words in its name, its sender or its subject, from what is stored of
//! each (its name, type and size) and nothing fetched. A file is downloaded by the key and folder of the copy that
//! holds it, through `/api/message/attachment`.

use std::collections::{HashMap, HashSet};
use std::fmt;
use std::sync::Arc;

use mm_kernel::ago::rel_time;
use mm_kernel::errors::Fault;
use mm_kernel::seams::Clock;
use serde_json::{Map, Value, json};

use crate::ports::{AccountNames, Row, StoredDocuments};

/// Which kind of file a document is, in the order a page in sections by type shows them (`DocumentKind`).
pub const KINDS: [&str; 8] = ["pdf", "image", "doc", "sheet", "slide", "archive", "text", "other"];

/// What sections the files can be shown in (`DocumentGrouping`).
pub const GROUPINGS: [&str; 2] = ["sender", "type"];

/// Calendar invites, signatures and keys, which aren't documents.
const EXCLUDE: [&str; 7] = [
    "text/calendar",
    "application/ics",
    "pkcs7",
    "pgp-signature",
    "pgp-keys",
    "x-pkcs7",
    "application/x-pkcs7-signature",
];

const IMAGE_TYPES: [&str; 5] = ["image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"];
const IMAGE_EXTENSIONS: [&str; 5] = ["png", "jpg", "jpeg", "gif", "webp"];

/// A column as stored, null when the row hasn't it.
fn col<'a>(r: &'a Row, name: &str) -> &'a Value {
    r.get(name).unwrap_or(&Value::Null)
}

/// A text column's text, "" when it is null or not text (`r[k] or ""`).
fn text<'a>(r: &'a Row, name: &str) -> &'a str {
    col(r, name).as_str().unwrap_or_default()
}

/// Python's truth of a stored value.
fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `a or b or c`, for text columns: the first that isn't empty, else "".
fn first<'a>(r: &'a Row, names: &[&str]) -> &'a str {
    names.iter().map(|name| text(r, name)).find(|t| !t.is_empty()).unwrap_or_default()
}

/// What follows a name's last dot, "" when it has none.
fn extension(name: &str) -> &str {
    name.rsplit_once('.').map_or("", |(_, ext)| ext)
}

/// Whether a type is a document's, not an invite's, a signature's or a key's.
pub fn is_document(content_type: &str) -> bool {
    let content_type = content_type.to_lowercase();
    !EXCLUDE.iter().any(|x| content_type.contains(x))
}

/// Which kind of file it is, by its type and by its name.
pub fn kind_of(content_type: &str, filename: &str) -> &'static str {
    let (ct, name) = (content_type.to_lowercase(), filename.to_lowercase());
    let ext = extension(&name);
    let ct = ct.as_str();
    if ct.contains("pdf") || ext == "pdf" {
        "pdf"
    } else if ct.starts_with("image/")
        || ["png", "jpg", "jpeg", "gif", "webp", "heic", "svg", "bmp", "tiff"].contains(&ext)
    {
        "image"
    } else if ["doc", "docx", "odt", "rtf", "pages"].contains(&ext)
        || ct.contains("msword")
        || ct.contains("wordprocessingml")
        || ct.contains("opendocument.text")
    {
        "doc"
    } else if ["xls", "xlsx", "ods", "csv", "numbers", "tsv"].contains(&ext)
        || ct.contains("spreadsheet")
        || ct.contains("ms-excel")
        || ct == "text/csv"
    {
        "sheet"
    } else if ["ppt", "pptx", "odp", "key"].contains(&ext) || ct.contains("presentation") || ct.contains("powerpoint") {
        "slide"
    } else if ["zip", "rar", "7z", "tar", "gz", "tgz"].contains(&ext)
        || ct.contains("zip")
        || ct.contains("compress")
        || ct.contains("x-tar")
    {
        "archive"
    } else if ct.starts_with("text/") || ["txt", "md", "log", "json", "xml"].contains(&ext) {
        "text"
    } else {
        "other"
    }
}

/// Whether to offer a preview of a file: an image by its type, or by its name where its type says nothing.
pub fn previewable(content_type: &str, filename: &str) -> bool {
    let ct = content_type.to_lowercase();
    let ct = mm_kernel::text::strip(ct.split(';').next().unwrap_or_default());
    if ct.is_empty() || ct == "application/octet-stream" {
        let name = filename.to_lowercase();
        return name.contains('.') && IMAGE_EXTENSIONS.contains(&extension(&name));
    }
    IMAGE_TYPES.contains(&ct)
}

/// What a request asks for (`Asked`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Asked {
    pub limit: i64,
    pub offset: i64,
    /// One kind, or every kind ("").
    pub kind: String,
    /// Lowercase words in its name, its sender or its subject.
    pub needle: String,
    /// A grouping, or none ("").
    pub group: String,
}

/// Python's `int()` of a string, held at the ends of an i64 past them: None for what doesn't read as a number.
fn python_int(given: &str) -> Option<i64> {
    let text = mm_kernel::text::strip(given);
    let (negative, digits) = match text.as_bytes().first() {
        Some(b'-') => (true, &text[1..]),
        Some(b'+') => (false, &text[1..]),
        _ => (false, text),
    };
    let well_formed = !digits.is_empty()
        && !digits.starts_with('_')
        && !digits.ends_with('_')
        && !digits.contains("__")
        && digits.chars().all(|c| c.is_ascii_digit() || c == '_');
    if !well_formed {
        return None;
    }
    let mut n: i64 = 0;
    for d in digits.bytes().filter(u8::is_ascii_digit) {
        let d = i64::from(d - b'0');
        n = n.saturating_mul(10).saturating_add(if negative { -d } else { d });
    }
    Some(n)
}

/// What a request asks for (`documents.asked`), from each name's value as given: a limit or an offset that won't
/// read is its default, since a page in the wrong place harms no one.
pub fn asked(
    limit: Option<&str>,
    offset: Option<&str>,
    kind: Option<&str>,
    q: Option<&str>,
    group: Option<&str>,
) -> Asked {
    let given = |v: Option<&str>, default: &'static str| v.filter(|v| !v.is_empty()).unwrap_or(default).to_owned();
    Asked {
        limit: python_int(&given(limit, "400")).map_or(400, |n| n.clamp(1, 1000)),
        offset: python_int(&given(offset, "0")).map_or(0, |n| n.max(0)),
        kind: kind.unwrap_or_default().to_owned(),
        needle: mm_kernel::text::strip(q.unwrap_or_default()).to_lowercase(),
        group: group.unwrap_or_default().to_owned(),
    }
}

/// What names one physical file: the same message synced to two accounts or folders is one file, by its
/// Message-ID, size and name, and by its row where it has no Message-ID.
fn same_file(r: &Row) -> String {
    let name = text(r, "filename").to_lowercase();
    if truthy(col(r, "message_id")) {
        json!([col(r, "message_id"), col(r, "size"), name]).to_string()
    } else {
        json!([col(r, "pk"), name]).to_string()
    }
}

fn holds(r: &Row, needle: &str) -> bool {
    needle.is_empty()
        || ["filename", "from_name", "from_addr", "subject"]
            .map(|k| text(r, k))
            .join(" ")
            .to_lowercase()
            .contains(needle)
}

/// What a file's section goes by, and what it is called: by type its kind; by sender their address, so one person
/// writing under two names is one section.
fn section_of(r: &Row, kind: &'static str, by: &str) -> (String, String) {
    if by == "type" {
        return (kind.to_owned(), kind.to_owned());
    }
    let name = first(r, &["from_name", "from_addr"]);
    let addr = text(r, "from_addr");
    ((if addr.is_empty() { name } else { addr }).to_lowercase(), name.to_owned())
}

/// The matches, each section's together, and the sections in the same order: by type the kinds' order, by sender
/// whoever sent the newest file first. Inside a section they stay as they came.
fn in_sections<'a>(matches: Vec<(&'a Row, &'static str)>, by: &str) -> (Vec<(&'a Row, &'static str)>, Vec<Value>) {
    let mut sections: Vec<(String, String, usize)> = Vec::new();
    let mut at_of: HashMap<String, usize> = HashMap::new();
    for (r, kind) in &matches {
        let (at, name) = section_of(r, kind, by);
        match at_of.get(&at) {
            Some(&i) => sections[i].2 += 1,
            None => {
                at_of.insert(at.clone(), sections.len());
                sections.push((at, name, 1));
            }
        }
    }
    let rank: HashMap<String, usize> = if by == "type" {
        KINDS.iter().enumerate().map(|(i, k)| ((*k).to_owned(), i)).collect()
    } else {
        sections.iter().enumerate().map(|(i, s)| (s.0.clone(), i)).collect()
    };
    let rank_of = |at: &str| rank.get(at).copied().unwrap_or(usize::MAX);
    let mut matches = matches;
    matches.sort_by_key(|(r, kind)| rank_of(&section_of(r, kind, by).0));
    sections.sort_by_key(|s| rank_of(&s.0));
    let sections = sections.into_iter().map(|(at, name, count)| json!({"id": at, "name": name, "count": count}));
    (matches, sections.collect())
}

/// `urllib.parse.quote_plus` of a value as `str()` writes it.
fn quote_plus(v: &Value) -> String {
    let text = match v {
        Value::Null => "None".to_owned(),
        Value::String(s) => s.clone(),
        other => other.to_string(),
    };
    let mut out = String::with_capacity(text.len());
    for byte in text.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'_' | b'.' | b'-' | b'~' => out.push(char::from(byte)),
            b' ' => out.push('+'),
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}

/// Real attachments in the order `rows` holds them, or in sections when asked: the `limit` from `offset` of those
/// that match (`matched` counts them), with each kind's count of them all. `when` says a date as the list does.
pub fn page(rows: &[Row], want: &Asked, when: impl Fn(Option<&str>) -> String) -> Value {
    let mut matches: Vec<(&Row, &'static str)> = Vec::new();
    let mut facets: Map<String, Value> = Map::new();
    let mut seen: HashSet<String> = HashSet::new();
    for r in rows {
        if !is_document(text(r, "content_type")) {
            continue;
        }
        let kind = kind_of(text(r, "content_type"), text(r, "filename"));
        if !seen.insert(same_file(r)) {
            continue;
        }
        let count = facets.get(kind).and_then(Value::as_i64).unwrap_or(0);
        facets.insert(kind.to_owned(), json!(count + 1));
        if !want.kind.is_empty() && kind != want.kind {
            continue;
        }
        if !holds(r, &want.needle) {
            continue;
        }
        matches.push((r, kind));
    }
    let mut sections = Vec::new();
    if !want.group.is_empty() {
        (matches, sections) = in_sections(matches, &want.group);
    }
    let total: i64 = facets.values().filter_map(Value::as_i64).sum();
    let from = usize::try_from(want.offset).unwrap_or(usize::MAX).min(matches.len());
    let to = from.saturating_add(usize::try_from(want.limit).unwrap_or(0)).min(matches.len());
    let docs: Vec<Value> = matches[from..to]
        .iter()
        .map(|(r, kind)| {
            let names = format!(
                "key={}&folder={}&uid={}&part_id={}",
                quote_plus(col(r, "message_key")),
                quote_plus(col(r, "folder")),
                quote_plus(col(r, "uid")),
                quote_plus(col(r, "part_id")),
            );
            let preview = previewable(text(r, "content_type"), text(r, "filename"))
                .then(|| format!("/api/message/attachment/preview.html?{names}"));
            json!({
                "key": col(r, "message_key"),
                "folder": col(r, "folder"),
                "uid": col(r, "uid"),
                "part_id": col(r, "part_id"),
                "filename": col(r, "filename"),
                "kind": kind,
                "size": if truthy(col(r, "size")) { col(r, "size").clone() } else { json!(0) },
                "account": col(r, "account"),
                "from": first(r, &["from_name", "from_addr"]),
                "subject": if truthy(col(r, "subject")) { col(r, "subject").clone() } else { json!("(no subject)") },
                "when": when(col(r, "date_iso").as_str()),
                "url": format!("/api/message/attachment?{names}"),
                "preview": preview,
                "group": if want.group.is_empty() { String::new() } else { section_of(r, kind, &want.group).0 },
            })
        })
        .collect();
    json!({
        "ok": true,
        "total": total,
        "matched": matches.len(),
        "shown": docs.len(),
        "facets": facets,
        "groups": sections,
        "docs": docs,
    })
}

/// The Document Hub, over the accounts every other corpus-wide read goes over.
pub struct DocumentHub {
    stored: Arc<dyn StoredDocuments>,
    accounts: Arc<dyn AccountNames>,
    clock: Arc<dyn Clock>,
}

impl fmt::Debug for DocumentHub {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("DocumentHub").finish_non_exhaustive()
    }
}

impl DocumentHub {
    pub fn new(
        stored: Arc<dyn StoredDocuments>,
        accounts: Arc<dyn AccountNames>,
        clock: Arc<dyn Clock>,
    ) -> DocumentHub {
        DocumentHub { stored, accounts, clock }
    }

    /// A page of the documents, as `want` asks.
    pub fn page(&self, want: &Asked) -> Result<Value, Fault> {
        let accounts = self.accounts.emails()?;
        let rows = if accounts.is_empty() { Vec::new() } else { self.stored.attachments(&accounts)? };
        let now = self.clock.now();
        Ok(page(&rows, want, |iso| rel_time(iso, now)))
    }
}
