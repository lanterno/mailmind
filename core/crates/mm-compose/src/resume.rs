//! A saved draft, opened back up (`compose.domain.resume`): which copy may be believed, what you typed, the quote it
//! carries and its files.
//!
//! A draft's bytes are read as the Python app read them: cut into parts as its `email` package cut them (`parts`),
//! its headers as `email.policy.default` read them (`headers`), and its text, HTML and files by the core's
//! `mime::parse` and `extract_part`, which the Python app called too. Every part must come back as the text or a
//! file, as Python's parts checked against what `parse` lists say, or the draft is refused.

mod headers;
mod parts;

use std::collections::{BTreeSet, HashSet};
use std::sync::LazyLock;

use mm_content::mime;
use mm_kernel::addresses::parse_addr_list;
use mm_kernel::errors::{Kind, Problem};
use mm_kernel::text::strip;
use regex::Regex;
use sha2::{Digest, Sha256};

use crate::markdown;
use crate::spec::{MAX_ATTACH_BYTES, MAX_SEND_POST, RESUME_HEADROOM};

pub use headers::addr_chip;

/// What every refusal to reopen a draft ends with (`resume.ELSEWHERE`).
pub const ELSEWHERE: &str = " Edit it in the app that made it.";
/// A draft whose bytes can't be read (`resume.UNREADABLE`).
pub const UNREADABLE: &str = "MailMind can’t read this draft. Edit it in the app that made it.";
/// The modes a reply opens in (`resume.REPLIES`).
pub const REPLIES: [&str; 2] = ["reply", "replyall"];
/// What a composer is (`resume.MODES`).
const MODES: [&str; 4] = ["new", "reply", "replyall", "forward"];

const FORMATTED_ONLY: &str =
    "This draft only has formatted text. Edit it in the app that made it, so its links and formatting aren’t lost.";
const TOO_LARGE: &str = "This draft is too large for MailMind to save or send, with its files and the text it quotes. \
                         Edit it in the app that made it.";
const LOST: &str =
    "Part of this draft is something MailMind can’t reopen without losing it. Edit it in the app that made it.";
const INVITE: &str =
    "This draft is a calendar invite, which MailMind can’t send on as one. Edit it in the app that made it.";
const TOO_MANY_FILES: &str =
    "This draft’s files add up to more than 25 MB, more than MailMind can send. Edit it in the app that made it.";

fn pattern(source: &str) -> Regex {
    Regex::new(source).expect("a pattern written in this crate compiles")
}

/// `resume.PARTICIPANT`, Python's `\s` being `str.isspace`.
static PARTICIPANT: LazyLock<Regex> = LazyLock::new(|| pattern(r#"[^\s\x1C-\x1F<>,;"]+@[^\s\x1C-\x1F<>,;"]+"#));
/// A name the core made up for a picture with no name of its own.
static MADE_UP_NAME: LazyLock<Regex> = LazyLock::new(|| pattern(r"\Ainline-\d+\z"));

fn unreadable(said: &str) -> Problem {
    Problem::new(Kind::Unreadable, said)
}

/// `resume.lf`: text with LF line ends only.
pub fn lf(s: &str) -> String {
    s.replace("\r\n", "\n").replace('\r', "\n")
}

/// `resume.sha_of`: the SHA-256 of a draft's bytes, in hex.
pub fn sha_of(raw: &[u8]) -> String {
    Sha256::digest(raw).iter().map(|byte| format!("{byte:02x}")).collect()
}

/// What a draft's record may be believed of, for the copy being reopened (`resume.Trust`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Trust {
    /// The very copy the row tracks.
    pub same_copy: bool,
    /// That, or the very bytes it recorded: only then does what it says hold for this copy.
    pub exact: bool,
}

/// What a draft's row says of the copy it tracks.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct TrackedCopy {
    pub folder: Option<String>,
    pub uid: Option<i64>,
    pub uidvalidity: Option<i64>,
    /// The SHA-256 of the bytes MailMind last saved, in hex.
    pub raw_sha: Option<String>,
}

/// `resume.trust`: `d` is the draft's row, the copy opened is in `row_folder` at `row_uid` with `raw` its bytes, and
/// `uidvalidity` is its folder's epoch now. The same copy is the same folder and UID, in the same epoch where both are
/// known. A server may rewrite the bytes of a copy it keeps, and another app may give the same bytes a new UID, so
/// either vouches.
pub fn trust(d: &TrackedCopy, row_folder: &str, row_uid: Option<i64>, uidvalidity: Option<i64>, raw: &[u8]) -> Trust {
    let same = d.uid.is_some()
        && d.uid == row_uid
        && d.folder.as_deref() == Some(row_folder)
        && (d.uidvalidity.is_none() || uidvalidity.is_none() || d.uidvalidity == uidvalidity);
    let exact = same || d.raw_sha.as_deref().is_some_and(|sha| !sha.is_empty() && sha == sha_of(raw));
    Trust { same_copy: same, exact }
}

/// `resume.copy_moved`: did the draft's row move while this waited for the gate? Each is the row's (uid, message_id),
/// None where there is no row. A save or a send that held it may have replaced or removed the copy the row tracked.
pub fn copy_moved(before: Option<(Option<i64>, Option<String>)>, now: Option<(Option<i64>, Option<String>)>) -> bool {
    before.is_some_and(|before| now.is_none_or(|now| now != before))
}

/// `resume.copy_is_there`: what a probe of a copy says, given the epoch it saw and the one held. Every UID held for a
/// folder renumbered is void.
pub fn copy_is_there(uidvalidity: Option<i64>, known: Option<i64>, there: bool) -> bool {
    match (uidvalidity, known) {
        (Some(seen), Some(known)) if seen != known => false,
        _ => there,
    }
}

/// What reopening reads of a draft's bytes: its headers as `email.policy.default` read them (`header_text`,
/// `header_chips`), and its text and HTML as `mime.parse` reads them.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Draft {
    /// The Subject, its encoded words decoded, its folds unfolded; None where there is none.
    pub subject: Option<String>,
    pub to: Vec<String>,
    pub cc: Vec<String>,
    /// The Bcc another app kept in the draft itself.
    pub bcc: Vec<String>,
    /// In-Reply-To as the header registry writes it out: what `thread_ids` takes each `<id>` from.
    pub in_reply_to: Option<String>,
    /// References, likewise.
    pub references: Option<String>,
    pub text: Option<String>,
    pub html: Option<String>,
}

/// `resume.read`, and the headers `_opened` reads of what it gives. Unreadable where Python's parser raised: parts
/// nested deeper than its stack held.
pub fn read(raw: &[u8]) -> Result<Draft, Problem> {
    let tree = parts::Tree::parse(raw).ok_or_else(|| unreadable(UNREADABLE))?;
    let headers = &tree.parts[0].headers;
    let parsed = mime::parse(raw);
    Ok(Draft {
        subject: headers::header_text(headers, "Subject"),
        to: headers::header_chips(headers, "To"),
        cc: headers::header_chips(headers, "Cc"),
        bcc: headers::header_chips(headers, "Bcc"),
        in_reply_to: headers::header_ids(headers, "In-Reply-To"),
        references: headers::header_ids(headers, "References"),
        text: parsed.text.map(|text| text.into_owned()),
        html: parsed.html.map(|html| html.into_owned()),
    })
}

/// A file as the composer takes it back (`resume.ResumedFile`). `cid` is the token its mark in the text names it by.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResumedFile {
    pub filename: String,
    pub content_type: String,
    pub size: usize,
    pub b64: String,
    pub cid: Option<String>,
}

/// `resume.resume_files`: a draft's files as the composer takes them, and how many were pictures in the text. Every
/// part must come back as the text or a file, or this refuses the draft (Unreadable). A file's bytes are what
/// `extract_part` gives, so an attached email comes as it was sent, where Python wrote it out again.
pub fn resume_files(raw: &[u8]) -> Result<(Vec<ResumedFile>, usize), Problem> {
    let tree = parts::Tree::parse(raw).ok_or_else(|| unreadable(UNREADABLE))?;
    let parsed = mime::parse(raw);
    let all = tree.walk(0);
    // Data in a "container" with no boundary: no reader lists it.
    if all.iter().any(|&p| tree.main_type(p) == "multipart" && !tree.is_multipart(p) && tree.holds_text(p)) {
        return Err(unreadable(LOST));
    }
    let leaves: Vec<usize> = all.iter().copied().filter(|&p| tree.main_type(p) != "multipart").collect();
    let leaf = |a: &mime::Attachment| (a.part_id as usize).checked_sub(1).and_then(|at| leaves.get(at)).copied();
    // The core read its parts otherwise than Python does here.
    if parsed.attachments.iter().any(|a| leaf(a).is_none_or(|p| tree.content_type(p) != a.content_type)) {
        return Err(unreadable(LOST));
    }
    // A delivery report and the like: its file wouldn't be whole.
    if leaves.iter().any(|&p| tree.main_type(p) == "message" && tree.content_type(p) != "message/rfc822") {
        return Err(unreadable(LOST));
    }
    let listed: HashSet<usize> = parsed.attachments.iter().map(|a| a.part_id as usize).collect();
    let body = (1..=leaves.len())
        .find(|at| !listed.contains(at) && tree.content_type(leaves[at - 1]) == "text/plain")
        .map(|at| leaves[at - 1]);
    // HTML that the text `parse` read is the alternative to.
    let mut covered: HashSet<usize> = HashSet::new();
    for &p in &all {
        if tree.content_type(p) == "multipart/alternative" && body.is_some_and(|body| tree.children(p).contains(&body))
        {
            covered.extend(tree.walk(p).into_iter().filter(|&x| tree.content_type(x) == "text/html"));
        }
    }
    let mut html_alone = false;
    for (index, &p) in leaves.iter().enumerate() {
        let content_type = tree.content_type(p);
        let named = tree.filename(p).is_some_and(|name| !name.is_empty());
        if content_type == "text/calendar" && !named && tree.disposition(p).as_deref() != Some("attachment") {
            return Err(unreadable(INVITE));
        }
        if listed.contains(&(index + 1)) || body == Some(p) || covered.contains(&p) {
            continue; // a file, the text, or formatting of that same text
        }
        if content_type == "text/html" && body.is_none() && !html_alone {
            html_alone = true; // formatted text only: `split_to_fit` refuses it as such
            continue;
        }
        if tree.decoded(p).unwrap_or_default().iter().all(|byte| b" \t\n\r\x0b\x0c".contains(byte)) {
            continue; // nothing in it to lose
        }
        return Err(unreadable(LOST));
    }
    let mut files = Vec::with_capacity(parsed.attachments.len());
    let (mut total, mut inline) = (0usize, 0usize);
    for a in &parsed.attachments {
        let data = mime::extract_part(raw, a.part_id).map(|(data, _)| data).unwrap_or_default();
        let content_type = leaf(a).map_or_else(|| a.content_type.clone(), |p| tree.content_type(p).to_owned());
        total += data.len();
        if total > MAX_ATTACH_BYTES {
            return Err(unreadable(TOO_MANY_FILES));
        }
        let mut filename = a.filename.clone();
        let token = if a.is_inline { markdown::token_of(a.content_id.as_deref()) } else { None };
        if a.content_id.as_deref().is_some_and(|cid| !cid.is_empty()) && a.is_inline && token.is_none() {
            inline += 1; // a picture in the text: it comes back as a file
            if MADE_UP_NAME.is_match(&filename) {
                filename.push_str(guess_extension(&content_type));
            }
        }
        files.push(ResumedFile { filename, content_type, size: data.len(), b64: base64(&data), cid: token });
    }
    Ok((files, inline))
}

/// `resume.resume_split`: a saved draft's text/plain split into (own, tail, how), or None when that can't be done
/// without losing something. `own_text` and `tail_text` are what MailMind's last save of this draft recorded the
/// composer held, and `exact` says this is the very copy it saved, so the record is the truth even when the server
/// rewrote the bytes. `how` is `exact` (the record), `tail` (the text still ends with the recorded quote, so only that
/// is cut) or `whole` (none of it can be told apart, so all of it is yours to edit).
pub fn resume_split(
    text: Option<&str>,
    own_text: Option<&str>,
    tail_text: Option<&str>,
    exact: bool,
) -> Option<(String, String, &'static str)> {
    if exact && let Some(own) = own_text {
        return Some((lf(own), lf(tail_text.unwrap_or_default()), "exact"));
    }
    let t = lf(text?);
    let tail = lf(tail_text.unwrap_or_default());
    let (s, b) = (tail.trim_end_matches('\n'), t.trim_end_matches('\n'));
    if s.starts_with("\n\n") && b.ends_with(s) {
        return Some((b[..b.len() - s.len()].to_owned(), tail.clone(), "tail"));
    }
    let own = t.strip_suffix('\n').unwrap_or(&t); // set_content's own trailing newline
    Some((own.to_owned(), String::new(), "whole"))
}

/// `resume.split_to_fit`: `resume_split`'s answer, if the draft can come back at all. Unreadable with formatted text
/// only, whose links and formatting would be lost, and past what a save can carry: every save and send of it carries
/// the files as base64 and the text and quote as JSON, so past `MAX_SEND_POST` each would be refused, and edits made
/// in the composer lost on close.
pub fn split_to_fit(
    text: Option<&str>,
    own_text: Option<&str>,
    tail_text: Option<&str>,
    exact: bool,
    files: &[ResumedFile],
) -> Result<(String, String, &'static str), Problem> {
    let split = resume_split(text, own_text, tail_text, exact).ok_or_else(|| unreadable(FORMATTED_ONLY))?;
    let size = files.iter().map(|file| file.b64.len()).sum::<usize>() + json_len(&split.0) + json_len(&split.1);
    if size + RESUME_HEADROOM > MAX_SEND_POST {
        return Err(unreadable(TOO_LARGE));
    }
    Ok(split)
}

/// How many bytes `json.dumps(text, ensure_ascii=False)` comes to in UTF-8: the quotes, a quote or backslash and the
/// five controls with a short escape two each, any other control six (`\u00XX`), the rest as they are.
fn json_len(text: &str) -> usize {
    2 + text
        .chars()
        .map(|c| match c {
            '"' | '\\' | '\n' | '\r' | '\t' | '\u{8}' | '\u{c}' => 2,
            c if c < ' ' => 6,
            c => c.len_utf8(),
        })
        .sum::<usize>()
}

/// `resume.loses_formatting`: whether a draft comes back as bare text with formatting beside it that saving would
/// lose. Not where that formatting is only what this text's Markdown comes to, which the next save writes again.
/// `render` is `markdown.render`.
pub fn loses_formatting(
    how: &str,
    own: &str,
    html: Option<&str>,
    files: &[ResumedFile],
    render: &dyn Fn(&str, &BTreeSet<String>) -> Option<String>,
) -> bool {
    let Some(html) = html.filter(|html| !html.is_empty()) else { return false };
    if how != "whole" {
        return false;
    }
    let pictures: BTreeSet<String> = files.iter().filter_map(|file| file.cid.clone()).collect();
    strip(&lf(html)) != strip(&render(own, &pictures).unwrap_or_default())
}

/// `resume.mode_of`: how a draft reopens. MailMind's own new drafts never carry an In-Reply-To, so one that does was
/// written as a reply.
pub fn mode_of(stored: Option<&str>, in_reply_to: Option<&str>) -> &'static str {
    let mode = MODES.iter().find(|mode| Some(**mode) == stored).copied().unwrap_or("new");
    if mode == "new" && in_reply_to.is_some_and(|irt| !irt.is_empty()) { "reply" } else { mode }
}

/// `resume.participants_of`: who a reply-all drafted from this original could reach, each once, lowercase, as far as
/// the stored row names them (its `from_addr` and `recipients`).
pub fn participants_of(from_addr: Option<&str>, recipients: Option<&str>) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let found = PARTICIPANT.find_iter(recipients.unwrap_or_default()).map(|found| found.as_str());
    for a in std::iter::once(from_addr.unwrap_or_default()).chain(found) {
        let a = strip(a).to_lowercase();
        if !a.is_empty() && !out.contains(&a) {
            out.push(a);
        }
    }
    out
}

/// `resume._address_of`: a chip's address, lowercase, or empty where it holds none.
fn address_of(chip: &str) -> String {
    parse_addr_list(&[chip])
        .and_then(|pairs| pairs.into_iter().next())
        .map(|(_, addr)| addr.to_lowercase())
        .unwrap_or_default()
}

/// `resume.bcc_of`: (the draft's Bcc as chips, how many were dropped). `header_bcc` is the Bcc another app may keep in
/// the draft itself, and `recorded` what MailMind recorded (a JSON list of chips). That joins it only where the record
/// is this copy's, and is otherwise counted, so the composer can say some went. A record that isn't a JSON list of
/// text counts as none, where Python raised.
pub fn bcc_of(header_bcc: &[String], recorded: Option<&str>, exact: bool) -> (Vec<String>, usize) {
    let had: HashSet<String> = header_bcc.iter().map(|chip| address_of(chip)).collect();
    let kept: Vec<String> = recorded
        .filter(|recorded| !recorded.is_empty())
        .and_then(|recorded| serde_json::from_str(recorded).ok())
        .unwrap_or_default();
    let missing: Vec<String> = kept
        .into_iter()
        .filter(|chip| {
            let addr = address_of(chip);
            !addr.is_empty() && !had.contains(&addr)
        })
        .collect();
    if exact {
        return ([header_bcc, &missing].concat(), 0);
    }
    (header_bcc.to_vec(), missing.len())
}

/// `resume.subject_of`: as drafted. Failing that, what the row said.
pub fn subject_of(subject: Option<&str>, then: Option<&str>) -> String {
    subject.or(then).unwrap_or_default().to_owned()
}

/// Standard base64, padded, on one line.
fn base64(data: &[u8]) -> String {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(data.len().div_ceil(3) * 4);
    for chunk in data.chunks(3) {
        let bits = chunk.iter().enumerate().fold(0u32, |bits, (at, &byte)| bits | u32::from(byte) << (16 - 8 * at));
        for at in 0..4 {
            if at <= chunk.len() {
                out.push(char::from(ALPHABET[(bits >> (18 - 6 * at)) as usize & 63]));
            } else {
                out.push('=');
            }
        }
    }
    out
}

/// `mimetypes.guess_extension` of a media type, by the table Python carries (`mimetypes._types_map_default`), or
/// nothing. Python read the machine's own `mime.types` files besides, which add types this table doesn't have.
fn guess_extension(content_type: &str) -> &'static str {
    match content_type.to_ascii_lowercase().as_str() {
        "application/epub+zip" => ".epub",
        "application/gzip" => ".gz",
        "application/json" => ".json",
        "application/manifest+json" => ".webmanifest",
        "application/msword" => ".doc",
        "application/n-quads" => ".nq",
        "application/n-triples" => ".nt",
        "application/octet-stream" => ".bin",
        "application/oda" => ".oda",
        "application/ogg" => ".ogx",
        "application/pdf" => ".pdf",
        "application/pkcs7-mime" => ".p7c",
        "application/postscript" => ".ps",
        "application/trig" => ".trig",
        "application/vnd.apple.mpegurl" => ".m3u",
        "application/vnd.ms-excel" => ".xls",
        "application/vnd.ms-fontobject" => ".eot",
        "application/vnd.ms-powerpoint" => ".ppt",
        "application/vnd.oasis.opendocument.graphics" => ".odg",
        "application/vnd.oasis.opendocument.presentation" => ".odp",
        "application/vnd.oasis.opendocument.spreadsheet" => ".ods",
        "application/vnd.oasis.opendocument.text" => ".odt",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation" => ".pptx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" => ".xlsx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document" => ".docx",
        "application/vnd.rar" => ".rar",
        "application/wasm" => ".wasm",
        "application/x-7z-compressed" => ".7z",
        "application/x-bcpio" => ".bcpio",
        "application/x-cpio" => ".cpio",
        "application/x-csh" => ".csh",
        "application/x-debian-package" => ".deb",
        "application/x-dvi" => ".dvi",
        "application/x-gtar" => ".gtar",
        "application/x-hdf" => ".hdf",
        "application/x-hdf5" => ".h5",
        "application/x-httpd-php" => ".php",
        "application/x-latex" => ".latex",
        "application/x-mif" => ".mif",
        "application/x-netcdf" => ".cdf",
        "application/x-pkcs12" => ".p12",
        "application/x-pn-realaudio" => ".ram",
        "application/x-python-code" => ".pyc",
        "application/x-rpm" => ".rpm",
        "application/x-sh" => ".sh",
        "application/x-shar" => ".shar",
        "application/x-shockwave-flash" => ".swf",
        "application/x-sv4cpio" => ".sv4cpio",
        "application/x-sv4crc" => ".sv4crc",
        "application/x-tar" => ".tar",
        "application/x-tcl" => ".tcl",
        "application/x-tex" => ".tex",
        "application/x-texinfo" => ".texi",
        "application/x-troff" => ".roff",
        "application/x-troff-man" => ".man",
        "application/x-troff-me" => ".me",
        "application/x-troff-ms" => ".ms",
        "application/x-ustar" => ".ustar",
        "application/x-wais-source" => ".src",
        "application/xml" => ".xsl",
        "application/yaml" => ".yaml",
        "application/zip" => ".zip",
        "audio/3gpp" => ".3gp",
        "audio/3gpp2" => ".3g2",
        "audio/aac" => ".aac",
        "audio/basic" => ".au",
        "audio/flac" => ".flac",
        "audio/matroska" => ".mka",
        "audio/mp4" => ".m4a",
        "audio/mpeg" => ".mp3",
        "audio/ogg" => ".ogg",
        "audio/opus" => ".opus",
        "audio/vnd.wave" => ".wav",
        "audio/webm" => ".weba",
        "audio/x-aiff" => ".aif",
        "audio/x-pn-realaudio" => ".ra",
        "font/otf" => ".otf",
        "font/ttf" => ".ttf",
        "font/woff" => ".woff",
        "font/woff2" => ".woff2",
        "image/avif" => ".avif",
        "image/bmp" => ".bmp",
        "image/emf" => ".emf",
        "image/fits" => ".fits",
        "image/g3fax" => ".g3",
        "image/gif" => ".gif",
        "image/heic" => ".heic",
        "image/heif" => ".heif",
        "image/ief" => ".ief",
        "image/jp2" => ".jp2",
        "image/jpeg" => ".jpg",
        "image/jpm" => ".jpm",
        "image/jpx" => ".jpx",
        "image/png" => ".png",
        "image/svg+xml" => ".svg",
        "image/t38" => ".t38",
        "image/tiff" => ".tiff",
        "image/tiff-fx" => ".tfx",
        "image/vnd.microsoft.icon" => ".ico",
        "image/webp" => ".webp",
        "image/wmf" => ".wmf",
        "image/x-cmu-raster" => ".ras",
        "image/x-portable-anymap" => ".pnm",
        "image/x-portable-bitmap" => ".pbm",
        "image/x-portable-graymap" => ".pgm",
        "image/x-portable-pixmap" => ".ppm",
        "image/x-rgb" => ".rgb",
        "image/x-xbitmap" => ".xbm",
        "image/x-xpixmap" => ".xpm",
        "image/x-xwindowdump" => ".xwd",
        "message/rfc822" => ".eml",
        "model/gltf+json" => ".gltf",
        "model/gltf-binary" => ".glb",
        "model/stl" => ".stl",
        "text/css" => ".css",
        "text/csv" => ".csv",
        "text/html" => ".html",
        "text/javascript" => ".js",
        "text/markdown" => ".md",
        "text/n3" => ".n3",
        "text/plain" => ".txt",
        "text/richtext" => ".rtx",
        "text/rtf" => ".rtf",
        "text/tab-separated-values" => ".tsv",
        "text/vtt" => ".vtt",
        "text/x-python" => ".py",
        "text/x-rst" => ".rst",
        "text/x-setext" => ".etx",
        "text/x-sgml" => ".sgm",
        "text/x-vcard" => ".vcf",
        "text/xml" => ".xml",
        "video/matroska" => ".mkv",
        "video/matroska-3d" => ".mk3d",
        "video/mp4" => ".mp4",
        "video/mpeg" => ".mpeg",
        "video/ogg" => ".ogv",
        "video/quicktime" => ".mov",
        "video/vnd.avi" => ".avi",
        "video/webm" => ".webm",
        "video/x-m4v" => ".m4v",
        "video/x-ms-wmv" => ".wmv",
        "video/x-sgi-movie" => ".movie",
        _ => "",
    }
}
