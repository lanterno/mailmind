//! The mail as an archive (`export.archive`): each message as a .eml file under its account and folder, or each
//! folder as an mbox file, packed in a zip made in the data directory, its owner's alone, and gone with its download.
//! A message whose whole bytes were never fetched is written from its row: with its Date, which the Python app loses;
//! with its sender's name quoted, which Python's leaves to read as two senders; and on one line where a stored value
//! broke one, where Python's fails the whole archive. Two folders whose names come to the same path share one mbox
//! file, where Python's write over each other.

#![allow(clippy::unwrap_used)]

use std::collections::BTreeMap;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use flate2::read::DeflateDecoder;
use mm_kernel::seams::{Clock, Moment, Outside, Tripped};
use mm_store::export::{Exports, Form};
use mm_store::{Database, DbKey, Store};

#[derive(Debug)]
struct Frozen;

impl Clock for Frozen {
    fn now(&self) -> Moment {
        // 2026-11-02 12:00:00 UTC
        Moment::from_micros(1_793_620_800_000_000)
    }
}

/// Files may open, but those under a folder named `refused`.
#[derive(Debug, Default)]
struct Files {
    refused: Option<PathBuf>,
}

impl Outside for Files {
    fn open(&self, path: &Path) -> Result<(), Tripped> {
        match &self.refused {
            Some(refused) if path.starts_with(refused) => Err(Tripped(path.display().to_string())),
            _ => Ok(()),
        }
    }

    fn reach(&self, host: &str) -> Result<(), Tripped> {
        Err(Tripped(host.to_owned()))
    }

    fn run(&self, program: &str) -> Result<(), Tripped> {
        Err(Tripped(program.to_owned()))
    }
}

fn key() -> DbKey {
    DbKey::from_bytes([0x5e; 32])
}

const BODIES: &str = "CREATE TABLE bodies (message_pk INTEGER PRIMARY KEY, raw BLOB, fetched_at TEXT);";

/// A stored message whose whole bytes were fetched: a line of its text starts `From `.
const FETCHED: &[u8] = b"From: Lin <lin@example.test>\r\nSubject: Lunch\r\n\r\nFrom the cafe, at noon.\r\n>From me\r\n";

/// A data directory with a database of `rows` (`INSERT INTO messages` values) and the fetched message's body for
/// the row whose pk is 1, and the exports over it.
fn exports(home: &Path, rows: &str, outside: Files) -> (Exports, PathBuf) {
    let path = home.join("mailmind.db");
    Store::open(&path, &key()).unwrap().create_list_tables().unwrap();
    let database = Arc::new(Database::new(path, key(), Arc::new(Files::default())));
    database
        .write(|c| {
            c.execute_batch(BODIES)?;
            if !rows.is_empty() {
                c.execute_batch(&format!(
                    "INSERT INTO messages (pk, account, kind, folder, uid, message_id, thread_id, in_reply_to, refs, \
                     from_addr, from_name, subject, date_iso, ts, snippet, bucket, body_fetched) VALUES {rows};"
                ))?;
            }
            c.execute("INSERT INTO bodies VALUES (1, ?, '2026-11-02T12:00:00+00:00')", [FETCHED])?;
            Ok(())
        })
        .unwrap();
    let temporary = home.join("tmp");
    std::fs::create_dir(&temporary).unwrap();
    (Exports::new(database, Arc::new(outside), Arc::new(Frozen), temporary), home.to_path_buf())
}

const ROWS: &str = "
  (1, 'ada@example.test', 'imap', 'INBOX', 1, '<lunch@example.test>', 't1', NULL, NULL, 'lin@example.test', 'Lin',
   'Lunch', '2026-11-02T11:00:00+00:00', 1793617200, 'From the cafe', 'primary', 1),
  (2, 'ada@example.test', 'imap', 'Folders/Clients', 2, '<brief@example.test>', 't2', '<lunch@example.test>',
   '<a@example.test> <lunch@example.test>', 'jane@example.test', 'Doe, Jane', 'Brief' || char(8232) || 'two',
   '2026-11-01T08:05:09-05:30', 1793540109, 'The brief.', 'other', 0),
  (3, 'ada@example.test', 'imap', 'Folders_Clients', 3, NULL, NULL, NULL, NULL, 'kit@example.test', NULL, NULL, NULL,
   0, NULL, NULL, 0),
  (4, 'bea@example.test', 'imap', '[Gmail]/Sent Mail', 1, '<sent@example.test>', NULL, NULL, NULL,
   'bea@example.test', 'Bea', 'Sent', 'not a time', 5, 'Sent it', NULL, 0)";

/// Each entry of a zip, by name: its bytes, inflated, once its CRC-32 and size agree with them.
fn entries(zip: &[u8]) -> BTreeMap<String, Vec<u8>> {
    let u16_at = |at: usize| usize::from(u16::from_le_bytes([zip[at], zip[at + 1]]));
    let u32_at = |at: usize| u32::from_le_bytes(zip[at..at + 4].try_into().unwrap());
    let end = zip.len() - 22;
    assert_eq!(&zip[end..end + 4], b"PK\x05\x06");
    let mut at = u32_at(end + 16) as usize;
    let mut found = BTreeMap::new();
    for _ in 0..u16_at(end + 10) {
        assert_eq!(&zip[at..at + 4], b"PK\x01\x02");
        let (crc, compressed, size) = (u32_at(at + 16), u32_at(at + 20) as usize, u32_at(at + 24) as usize);
        let (name_len, extra_len, comment_len) = (u16_at(at + 28), u16_at(at + 30), u16_at(at + 32));
        let name = String::from_utf8(zip[at + 46..at + 46 + name_len].to_vec()).unwrap();
        let local = u32_at(at + 42) as usize;
        let data = local + 30 + u16_at(local + 26) + u16_at(local + 28);
        let mut bytes = Vec::new();
        DeflateDecoder::new(&zip[data..data + compressed]).read_to_end(&mut bytes).unwrap();
        assert_eq!(bytes.len(), size, "{name}");
        let mut sum = flate2::Crc::new();
        sum.update(&bytes);
        assert_eq!(sum.sum(), crc, "{name}");
        found.insert(name, bytes);
        at += 46 + name_len + extra_len + comment_len;
    }
    found
}

fn text(bytes: &[u8]) -> &str {
    std::str::from_utf8(bytes).unwrap()
}

#[test]
fn an_eml_archive_holds_each_message_under_its_account_and_folder() {
    let home = tempfile::tempdir().unwrap();
    let (exports, _) = exports(home.path(), ROWS, Files::default());
    let download = exports.archive(Form::Eml).unwrap();
    assert_eq!(
        (download.name.as_str(), download.content_type),
        ("mailmind-eml-20261102T120000Z.zip", "application/zip")
    );
    let zip = std::fs::read(&download.file).unwrap();
    assert_eq!(download.size, zip.len() as u64);
    let found = entries(&zip);
    assert_eq!(
        found.keys().collect::<Vec<_>>(),
        [
            "ada@example.test/Folders_Clients/3-3.eml",
            "ada@example.test/Folders_Clients/brief@example.test-2.eml",
            "ada@example.test/INBOX/lunch@example.test-1.eml",
            "bea@example.test/[Gmail]_Sent Mail/sent@example.test-4.eml",
        ]
    );
    // Its own bytes when they were fetched.
    assert_eq!(found["ada@example.test/INBOX/lunch@example.test-1.eml"], FETCHED);
    // Written from its row when they weren't: the sender's name quoted, so it names one; the Date in the offset it
    // was stored with; a line separator in the subject a space.
    assert_eq!(
        text(&found["ada@example.test/Folders_Clients/brief@example.test-2.eml"]),
        "From: \"Doe, Jane\" <jane@example.test>\r\nSubject: Brief two\r\nDate: Sun, 01 Nov 2026 08:05:09 -0530\r\n\
         Message-ID: <brief@example.test>\r\nIn-Reply-To: <lunch@example.test>\r\n\
         References: <a@example.test> <lunch@example.test>\r\nX-MailMind-Account: ada@example.test\r\n\
         X-MailMind-Folder: Folders/Clients\r\nX-MailMind-ThreadID: t2\r\nX-MailMind-Bucket: other\r\n\
         Content-Type: text/plain; charset=\"utf-8\"\r\nContent-Transfer-Encoding: 7bit\r\nMIME-Version: 1.0\r\n\r\n\
         The brief.\r\n"
    );
    // A row with next to nothing in it, and a date that doesn't read as one, which goes unsaid.
    assert_eq!(
        text(&found["ada@example.test/Folders_Clients/3-3.eml"]),
        "From: kit@example.test\r\nX-MailMind-Account: ada@example.test\r\nX-MailMind-Folder: Folders_Clients\r\n\
         Content-Type: text/plain; charset=\"utf-8\"\r\nContent-Transfer-Encoding: 7bit\r\nMIME-Version: 1.0\r\n\r\n\r\n"
    );
    let sent = text(&found["bea@example.test/[Gmail]_Sent Mail/sent@example.test-4.eml"]);
    assert!(sent.starts_with("From: Bea <bea@example.test>\r\nSubject: Sent\r\nMessage-ID: <sent@example.test>\r\n"));
}

#[test]
fn an_mbox_archive_holds_a_file_a_folder_and_two_named_alike_share_one() {
    let home = tempfile::tempdir().unwrap();
    let (exports, _) = exports(home.path(), ROWS, Files::default());
    let download = exports.archive(Form::Mbox).unwrap();
    assert_eq!(download.name, "mailmind-mbox-20261102T120000Z.zip");
    let found = entries(&std::fs::read(&download.file).unwrap());
    assert_eq!(
        found.keys().collect::<Vec<_>>(),
        [
            "ada@example.test/Folders_Clients.mbox",
            "ada@example.test/INBOX.mbox",
            "bea@example.test/[Gmail]_Sent Mail.mbox"
        ]
    );
    // A line of a message that starts `From `, after any `>`s, gets a `>` more.
    assert_eq!(
        text(&found["ada@example.test/INBOX.mbox"]),
        "From mailmind@localhost 2026-11-02T11:00:00+00:00\n\
         From: Lin <lin@example.test>\r\nSubject: Lunch\r\n\r\n>From the cafe, at noon.\r\n>>From me\r\n\n"
    );
    // "Folders/Clients" and "Folders_Clients" come to the same file, which holds one folder after the other.
    let both = text(&found["ada@example.test/Folders_Clients.mbox"]);
    assert_eq!(both.matches("From mailmind@localhost ").count(), 2);
    assert!(both.starts_with("From mailmind@localhost 2026-11-01T08:05:09-05:30\nFrom: \"Doe, Jane\""));
    assert!(both.contains("\r\n\r\nThe brief.\r\n\nFrom mailmind@localhost \nFrom: kit@example.test\r\n"));
}

/// Each file and folder under `root`, with its mode.
fn modes(root: &Path) -> Vec<(PathBuf, u32)> {
    use std::os::unix::fs::PermissionsExt;
    let mut found = Vec::new();
    for entry in std::fs::read_dir(root).unwrap() {
        let path = entry.unwrap().path();
        found.push((path.clone(), std::fs::metadata(&path).unwrap().permissions().mode() & 0o777));
        if path.is_dir() {
            found.extend(modes(&path));
        }
    }
    found
}

#[test]
fn the_archive_is_made_in_the_data_directory_its_owners_alone_and_goes_with_its_download() {
    let home = tempfile::tempdir().unwrap();
    let (exports, data) = exports(home.path(), ROWS, Files::default());
    let download = exports.archive(Form::Eml).unwrap();
    let folder = download.file.parent().unwrap().to_path_buf();
    assert_eq!(folder.parent().unwrap(), data);
    assert!(folder.file_name().unwrap().to_string_lossy().starts_with("mm-export-"));
    assert_eq!(modes(&folder), [(download.file.clone(), 0o600)]);
    assert_eq!(modes(&data).into_iter().find(|(path, _)| *path == folder).unwrap().1, 0o700);
    // Nothing of it in the folder for temporary files.
    assert_eq!(modes(&data.join("tmp")), []);
    drop(download);
    assert!(!folder.exists());
}

#[test]
fn an_archive_where_the_outside_refuses_is_not_made() {
    let home = tempfile::tempdir().unwrap();
    let refused = Files { refused: Some(home.path().to_path_buf()) };
    let (exports, data) = exports(home.path(), ROWS, refused);
    assert_eq!(exports.archive(Form::Mbox).unwrap_err().class, "Tripped");
    assert!(
        !std::fs::read_dir(&data).unwrap().any(|e| e.unwrap().file_name().to_string_lossy().starts_with("mm-export-"))
    );
}

#[test]
fn an_archive_of_no_mail_is_an_empty_zip() {
    let home = tempfile::tempdir().unwrap();
    let (exports, _) = exports(home.path(), "", Files::default());
    for form in [Form::Eml, Form::Mbox] {
        let download = exports.archive(form).unwrap();
        let zip = std::fs::read(&download.file).unwrap();
        assert_eq!(zip.len(), 22);
        assert!(entries(&zip).is_empty());
    }
}

#[test]
fn a_form_is_named_as_the_route_names_it() {
    assert_eq!(Form::named("eml"), Some(Form::Eml));
    assert_eq!(Form::named("mbox"), Some(Form::Mbox));
    for other in ["", "db", "pst", "EML"] {
        assert_eq!(Form::named(other), None);
    }
}
