//! What the body backfill reads, as the Python app's `bodies_to_fetch` and `still_without_body` read it: an account's
//! newest messages with no body text, none on its way out, but for those a round leaves out; and which of some still
//! have none.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::Arc;

use mm_kernel::seams::{Outside, Tripped};
use mm_mailbox::backfill_ports::BodiesWanted;
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

/// Ada's mail: four with no body text, one with an empty one, one on its way out; and Bea's one.
const MAIL: &str = "
INSERT INTO messages (pk, account, kind, folder, uid, ts, body_text, pending_removal, message_key) VALUES
  (1, 'ada@example.test', 'imap', 'INBOX', 1, 100, NULL, NULL, 1),
  (2, 'ada@example.test', 'imap', 'INBOX', 2, 400, NULL, NULL, 2),
  (3, 'ada@example.test', 'imap', 'Archive', 1, 300, NULL, NULL, 3),
  (4, 'ada@example.test', 'imap', 'INBOX', 3, 500, '', NULL, 4),
  (5, 'ada@example.test', 'imap', 'INBOX', 4, 600, NULL, 9, 5),
  (6, 'ada@example.test', 'imap', 'Sent', 1, 200, NULL, NULL, 6),
  (7, 'bea@example.test', 'proton', 'INBOX', 1, 700, NULL, NULL, 7);
";

fn database(dir: &Path) -> Database {
    let key = DbKey::from_bytes([0x5e; 32]);
    let path = dir.join("mailmind.db");
    Store::open(&path, &key).unwrap().create_list_tables().unwrap();
    let database = Database::new(path, key, Arc::new(Open));
    database.read(|c| c.execute_batch(MAIL)).unwrap();
    database
}

#[test]
fn an_accounts_newest_without_a_body_come_first_leaving_out_mail_on_its_way_out_and_what_a_round_skips() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path());
    assert_eq!(database.bodies_to_fetch("ada@example.test", &[], 12).unwrap(), [2, 3, 6, 1]);
    assert_eq!(database.bodies_to_fetch("ada@example.test", &[], 2).unwrap(), [2, 3]);
    assert_eq!(database.bodies_to_fetch("ada@example.test", &[3, 7], 2).unwrap(), [2, 6]);
    assert_eq!(database.bodies_to_fetch("bea@example.test", &[], 12).unwrap(), [7]);
    assert_eq!(database.bodies_to_fetch("cy@example.test", &[], 12).unwrap(), Vec::<i64>::new());
}

#[test]
fn which_still_have_no_body_text_of_those_asked() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path());
    let mut still = database.still_without_body(&[1, 4, 5, 99]).unwrap();
    still.sort_unstable();
    assert_eq!(still, [1, 5]);
    assert_eq!(database.still_without_body(&[]).unwrap(), Vec::<i64>::new());
}
