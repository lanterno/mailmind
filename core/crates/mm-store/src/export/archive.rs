//! The mail in a standard format (`export.export_eml`, `export.export_mbox`, packed as `export.archive` packs them):
//! each message as its own .eml file under its account and folder, or each folder's messages as one mboxrd file. A
//! message whose whole bytes are stored goes as they are; one whose aren't is written from what is stored of it, its
//! headers and the start of its text, with `X-MailMind-*` headers that keep its thread, folder and bucket.
//!
//! Where it differs from the Python app, the Python app is wrong, and its tests say so (`xfail`): a written message
//! keeps its date, which Python's loses; a sender's name with a comma or a colon in it names one sender, where
//! Python's names two or a group; a header with a line separator in it is kept on one line, where Python's fails the
//! whole archive; and two folders whose names are made the same for a path share one mbox file, where Python's
//! writes both over each other.

use std::collections::HashMap;
use std::io::{self, BufWriter, Seek, Write};

use mm_compose::builder::{Header, plain_message};
use mm_kernel::isoformat;
use mm_kernel::text::{is_space, strip};
use mm_kernel::when::days_from_civil;
use rusqlite::{CachedStatement, Connection, OptionalExtension};

use super::zip::Zip;

/// The formats an archive comes in (`ArchiveFormat`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Form {
    Eml,
    Mbox,
}

impl Form {
    /// The form a request names, or None for one the export doesn't offer.
    pub fn named(name: &str) -> Option<Form> {
        match name {
            "eml" => Some(Form::Eml),
            "mbox" => Some(Form::Mbox),
            _ => None,
        }
    }

    /// Its name, as the archive's file name carries it.
    pub fn name(self) -> &'static str {
        match self {
            Form::Eml => "eml",
            Form::Mbox => "mbox",
        }
    }
}

/// What stopped an archive part way: the database, the file it was written to, or a message no header could be
/// written for.
#[derive(Debug)]
pub(super) enum Stop {
    Storage,
    Unwritable,
    Refused,
}

impl From<rusqlite::Error> for Stop {
    fn from(_: rusqlite::Error) -> Stop {
        Stop::Storage
    }
}

impl From<io::Error> for Stop {
    fn from(_: io::Error) -> Stop {
        Stop::Unwritable
    }
}

/// What a stored message's row holds that the archive writes.
struct Row {
    pk: i64,
    account: String,
    folder: String,
    message_id: Option<String>,
    thread_id: Option<String>,
    in_reply_to: Option<String>,
    refs: Option<String>,
    from_addr: Option<String>,
    from_name: Option<String>,
    subject: Option<String>,
    date_iso: Option<String>,
    snippet: Option<String>,
    bucket: Option<String>,
    body_fetched: bool,
}

const ROW: &str = "pk, account, folder, message_id, thread_id, in_reply_to, refs, from_addr, from_name, subject, \
                   date_iso, snippet, bucket, body_fetched";

impl Row {
    fn read(r: &rusqlite::Row<'_>) -> rusqlite::Result<Row> {
        Ok(Row {
            pk: r.get(0)?,
            account: r.get(1)?,
            folder: r.get(2)?,
            message_id: r.get(3)?,
            thread_id: r.get(4)?,
            in_reply_to: r.get(5)?,
            refs: r.get(6)?,
            from_addr: r.get(7)?,
            from_name: r.get(8)?,
            subject: r.get(9)?,
            date_iso: r.get(10)?,
            snippet: r.get(11)?,
            bucket: r.get(12)?,
            body_fetched: r.get::<_, Option<i64>>(13)?.unwrap_or(0) != 0,
        })
    }

    /// Its folder, or INBOX for one with none.
    fn folder(&self) -> &str {
        if self.folder.is_empty() { "INBOX" } else { &self.folder }
    }
}

/// `_BAD`: what never goes in a file's name: a path's separators, the control characters, and what a shell reads.
fn is_bad(c: char) -> bool {
    c <= '\u{1f}' || "/\\:*?\"<>|".contains(c)
}

/// `_safe`: `name` fit to be part of a path: what can't be in one made `_`, the dots, underscores and spaces at its
/// ends dropped, each run of blanks one space, and at most 120 bytes of it, a character cut in two dropped; or
/// `fallback` when nothing is left.
fn safe(name: &str, fallback: &str) -> String {
    let mut replaced = String::with_capacity(name.len());
    let mut bad = false;
    for c in strip(name).chars() {
        if !is_bad(c) {
            replaced.push(c);
        } else if !bad {
            replaced.push('_');
        }
        bad = is_bad(c);
    }
    let trimmed = replaced.trim_matches(['.', '_', ' ']);
    let mut spaced = String::with_capacity(trimmed.len());
    let mut blank = false;
    for c in trimmed.chars() {
        if is_space(c) {
            if !blank {
                spaced.push(' ');
            }
            blank = true;
        } else {
            spaced.push(c);
            blank = false;
        }
    }
    let mut end = spaced.len().min(120);
    while !spaced.is_char_boundary(end) {
        end -= 1;
    }
    if end == 0 { fallback.to_owned() } else { spaced[..end].to_owned() }
}

/// `str.splitlines()` ends a line here.
fn is_break(c: char) -> bool {
    matches!(c, '\n' | '\r' | '\u{b}' | '\u{c}' | '\u{1c}'..='\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}')
}

/// `_hdr`: a stored value on one line, each run of line ends a space, and trimmed. Python's runs only CR and LF
/// together, so a value with another line end in it failed the whole archive.
fn one_line(value: Option<&str>) -> String {
    let value = value.unwrap_or_default();
    let mut out = String::with_capacity(value.len());
    let mut broke = false;
    for c in value.chars() {
        if is_break(c) {
            if !broke {
                out.push(' ');
            }
            broke = true;
        } else {
            out.push(c);
            broke = false;
        }
    }
    strip(&out).to_owned()
}

/// `email.utils.format_datetime(datetime.fromisoformat(written))`: the Date a stored time is written as, in the
/// offset it was stored with. None for one that doesn't read as a time.
fn date(written: &str) -> Option<String> {
    let time = isoformat::read(written)?;
    let wall = time.wall;
    let days = days_from_civil(i64::from(wall.year), wall.month, wall.day);
    let weekday = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][(days + 3).rem_euclid(7) as usize];
    let month = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        .get(wall.month as usize - 1)?;
    // `%z`: the offset as ±HHMM, its seconds and microseconds after when it has them.
    let zone = match time.offset {
        None => "-0000".to_owned(),
        Some(micros) => {
            let (sign, micros) = if micros < 0 { ('-', -micros) } else { ('+', micros) };
            let seconds = micros / 1_000_000;
            let mut zone = format!("{sign}{:02}{:02}", seconds / 3600, seconds % 3600 / 60);
            if micros % 60_000_000 != 0 {
                zone.push_str(&format!("{:02}", seconds % 60));
                if micros % 1_000_000 != 0 {
                    zone.push_str(&format!(".{:06}", micros % 1_000_000));
                }
            }
            zone
        }
    };
    Some(format!(
        "{weekday}, {:02} {month} {:04} {:02}:{:02}:{:02} {zone}",
        wall.day, wall.year, wall.hour, wall.minute, wall.second
    ))
}

/// `_synth`: the message written from its row: who sent it, its subject, date and threading, where it was and how it
/// was sorted, over the start of its text.
fn written(row: &Row) -> Result<Vec<u8>, Stop> {
    let mut headers: Vec<Header> = Vec::with_capacity(10);
    let (name, addr) = (one_line(row.from_name.as_deref()), one_line(row.from_addr.as_deref()));
    if !name.is_empty() || !addr.is_empty() {
        headers.push(Header::Person("From", name, addr));
    }
    let mut named = |header: &'static str, value: String| {
        if !value.is_empty() {
            headers.push(Header::Named(header, value));
        }
    };
    named("Subject", one_line(row.subject.as_deref()));
    named("Date", date(&one_line(row.date_iso.as_deref())).unwrap_or_default());
    named("Message-ID", one_line(row.message_id.as_deref()));
    named("In-Reply-To", one_line(row.in_reply_to.as_deref()));
    named("References", one_line(row.refs.as_deref()));
    named("X-MailMind-Account", one_line(Some(&row.account)));
    named("X-MailMind-Folder", one_line(Some(&row.folder)));
    named("X-MailMind-ThreadID", one_line(row.thread_id.as_deref()));
    named("X-MailMind-Bucket", one_line(row.bucket.as_deref()));
    plain_message(&headers, row.snippet.as_deref().unwrap_or_default()).map_err(|_| Stop::Refused)
}

/// `_raw_for`: the message's own bytes when they are stored, else the message written from its row.
fn raw_for(bodies: &mut CachedStatement<'_>, row: &Row) -> Result<Vec<u8>, Stop> {
    if row.body_fetched {
        let raw: Option<Option<Vec<u8>>> = bodies.query_row([row.pk], |r| r.get(0)).optional()?;
        if let Some(Some(raw)) = raw.filter(|raw| raw.as_ref().is_some_and(|r| !r.is_empty())) {
            return Ok(raw);
        }
    }
    written(row)
}

/// One message as an mboxrd file holds it: the `From ` line that starts it, its lines with a `>` before each that
/// starts `From ` after any `>`s, and a blank line after it.
fn mbox_message(out: &mut dyn Write, date_iso: Option<&str>, raw: &[u8]) -> io::Result<()> {
    out.write_all(b"From mailmind@localhost ")?;
    out.write_all(date_iso.unwrap_or_default().as_bytes())?;
    out.write_all(b"\n")?;
    for line in raw.split_inclusive(|b| *b == b'\n') {
        let quotes = line.iter().take_while(|b| **b == b'>').count();
        if line[quotes..].starts_with(b"From ") {
            out.write_all(b">")?;
        }
        out.write_all(line)?;
    }
    if !raw.ends_with(b"\n") {
        out.write_all(b"\n")?;
    }
    out.write_all(b"\n")
}

/// Every stored message into `zip`, in `form`, read in one block of the database.
pub(super) fn write_all<W: Write + Seek>(c: &Connection, zip: &mut Zip<W>, form: Form) -> Result<(), Stop> {
    let mut bodies = c.prepare_cached("SELECT raw FROM bodies WHERE message_pk=?")?;
    match form {
        Form::Eml => {
            let mut rows = c.prepare(&format!("SELECT {ROW} FROM messages ORDER BY account, folder, ts, pk"))?;
            let mut rows = rows.query([])?;
            while let Some(r) = rows.next()? {
                let row = Row::read(r)?;
                let raw = raw_for(&mut bodies, &row)?;
                let base =
                    safe(row.message_id.as_deref().unwrap_or_default().trim_matches(['<', '>']), &row.pk.to_string());
                let name =
                    format!("{}/{}/{base}-{}.eml", safe(&row.account, "acct"), safe(row.folder(), "folder"), row.pk);
                zip.entry(&name, Some(raw.len() as u64), |out| Ok::<(), Stop>(out.write_all(&raw)?))?;
            }
        }
        Form::Mbox => {
            // Each file and the folders it holds, in the order they first come: two folders whose names come to the
            // same path share one.
            let mut files: Vec<(String, Vec<(String, String)>)> = Vec::new();
            let mut at: HashMap<String, usize> = HashMap::new();
            let mut folders = c.prepare("SELECT DISTINCT account, folder FROM messages ORDER BY account, folder")?;
            let mut folders = folders.query([])?;
            while let Some(r) = folders.next()? {
                let (account, folder): (String, String) = (r.get(0)?, r.get(1)?);
                let shown = if folder.is_empty() { "INBOX" } else { &folder };
                let path = format!("{}/{}.mbox", safe(&account, "acct"), safe(shown, "folder"));
                let i = *at.entry(path.clone()).or_insert_with(|| {
                    files.push((path, Vec::new()));
                    files.len() - 1
                });
                files[i].1.push((account, folder));
            }
            let mut rows =
                c.prepare(&format!("SELECT {ROW} FROM messages WHERE account=? AND folder=? ORDER BY ts, pk"))?;
            for (path, members) in files {
                zip.entry(&path, None, |out| {
                    let mut out = BufWriter::with_capacity(super::CHUNK, out);
                    for (account, folder) in &members {
                        let mut each = rows.query([account, folder])?;
                        while let Some(r) = each.next()? {
                            let row = Row::read(r)?;
                            let raw = raw_for(&mut bodies, &row)?;
                            mbox_message(&mut out, row.date_iso.as_deref(), &raw)?;
                        }
                    }
                    out.flush()?;
                    Ok::<(), Stop>(())
                })?;
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_name_is_made_fit_for_a_path_as_python_makes_it() {
        assert_eq!(safe("ada@example.test", "acct"), "ada@example.test");
        assert_eq!(safe("Folders/Clients", "folder"), "Folders_Clients");
        assert_eq!(safe("[Gmail]/Sent Mail", "folder"), "[Gmail]_Sent Mail");
        assert_eq!(safe("a//b", "x"), "a_b");
        assert_eq!(safe("  ._.hidden._ ", "x"), "hidden");
        assert_eq!(safe("a \t\u{a0} b", "x"), "a _ b");
        assert_eq!(safe("a\u{2003}\u{2003}b", "x"), "a b");
        assert_eq!(safe("", "7"), "7");
        assert_eq!(safe("<>", "7"), "7");
        assert_eq!(safe(&"é".repeat(70), "x"), "é".repeat(60));
        assert_eq!(safe(&format!("a{}", "é".repeat(70)), "x"), format!("a{}", "é".repeat(59)));
        assert_eq!(safe("a_/b", "x"), "a__b");
        assert_eq!(safe("a_\u{1}_b", "x"), "a___b");
        assert_eq!(safe("._ \u{2003}x", "x"), " x");
    }

    #[test]
    fn a_stored_time_is_written_as_format_datetime_writes_it() {
        assert_eq!(date("2026-11-02T12:00:00+00:00").as_deref(), Some("Mon, 02 Nov 2026 12:00:00 +0000"));
        assert_eq!(date("2026-03-01T08:05:09-05:30").as_deref(), Some("Sun, 01 Mar 2026 08:05:09 -0530"));
        assert_eq!(date("2026-03-01T08:05:09").as_deref(), Some("Sun, 01 Mar 2026 08:05:09 -0000"));
        assert_eq!(date("2026-03-01T08:05:09+01:00:30").as_deref(), Some("Sun, 01 Mar 2026 08:05:09 +010030"));
        assert_eq!(date("not a time"), None);
        assert_eq!(date(""), None);
    }

    #[test]
    fn a_value_goes_on_one_line() {
        assert_eq!(one_line(Some(" a\r\n\r\nb\u{2028}c ")), "a b c");
        assert_eq!(one_line(None), "");
    }

    #[test]
    fn a_line_that_starts_from_gets_a_quote_before_it() {
        let mut out = Vec::new();
        mbox_message(&mut out, Some("2026-11-02T12:00:00+00:00"), b"Subject: x\r\n\r\nFrom here\n>From there\nFrom")
            .unwrap();
        assert_eq!(
            out,
            b"From mailmind@localhost 2026-11-02T12:00:00+00:00\nSubject: x\r\n\r\n>From here\n>>From there\nFrom\n\n"
        );
    }
}
