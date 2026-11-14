//! The copy of a message a plan's preview saw, as the store has it now (`copies::pk_in`): the newest in that folder,
//! none on its way out.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::Arc;

use mm_kernel::seams::{Outside, Tripped};
use mm_store::{Database, DbKey, Store};

#[derive(Debug)]
struct Open;

impl Outside for Open {
    fn open(&self, _path: &Path) -> Result<(), Tripped> {
        Ok(())
    }

    fn reach(&self, _host: &str) -> Result<(), Tripped> {
        Ok(())
    }

    fn run(&self, _program: &str) -> Result<(), Tripped> {
        Ok(())
    }
}

/// Message 1 in the Inbox twice (a list's copy beside the one sent straight) and in the Archive; message 2 in the Inbox
/// on its way out; message 3 only in the Archive.
const ROWS: &str = "
INSERT INTO messages (pk, account, kind, folder, uid, message_key, pending_removal) VALUES
  (10, 'ada@example.test', 'imap', 'INBOX', 7, 1, NULL),
  (11, 'ada@example.test', 'imap', 'INBOX', 8, 1, NULL),
  (12, 'ada@example.test', 'imap', 'Archive', 3, 1, NULL),
  (13, 'ada@example.test', 'imap', 'INBOX', 9, 2, 41),
  (14, 'ada@example.test', 'imap', 'Archive', 4, 3, NULL);
";

fn database(dir: &Path) -> Database {
    let key = DbKey::from_bytes([0x5e; 32]);
    let path = dir.join("mailmind.db");
    Store::open(&path, &key).unwrap().create_list_tables().unwrap();
    let database = Database::new(path, key, Arc::new(Open));
    database.read(|c| c.execute_batch(ROWS)).unwrap();
    database
}

#[test]
fn the_copy_in_a_folder_is_the_newest_there_and_none_on_its_way_out_or_elsewhere() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path());
    let pk_in = |key: i64, folder: &str| database.read(|c| mm_store::copies::pk_in(c, key, folder)).unwrap();
    assert_eq!(pk_in(1, "INBOX"), Some(11));
    assert_eq!(pk_in(1, "Archive"), Some(12));
    assert_eq!(pk_in(2, "INBOX"), None, "moved, and not listed");
    assert_eq!(pk_in(3, "INBOX"), None, "it is in the Archive");
    assert_eq!(pk_in(4, "INBOX"), None, "no such message");
}
