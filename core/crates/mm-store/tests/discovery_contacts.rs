//! Autocomplete's people, as the store finds them: who matches what was typed, and in what order.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::Arc;

use mm_discovery::ports::ContactMail;
use mm_kernel::seams::{Clock, Moment, Outside, Tripped};
use mm_store::discovery::DiscoveryStore;
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

#[derive(Debug)]
struct Frozen;

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000)
    }
}

/// Mae writes most and Ivo last, but neither name starts with "li": Lin's does, Mae's second word does, and Ivo's
/// has it only inside a word.
const PEOPLE: &str = "
INSERT INTO messages (account, kind, folder, uid, from_addr, from_name, ts, message_key) VALUES
  ('ada@example.test', 'imap', 'INBOX', 1, 'mae@guild.example.test', 'Mae Lindqvist', 100, 1),
  ('ada@example.test', 'imap', 'INBOX', 2, 'mae@guild.example.test', 'Mae Lindqvist', 200, 2),
  ('ada@example.test', 'imap', 'INBOX', 3, 'mae@guild.example.test', 'Mae Lindqvist', 300, 3),
  ('ada@example.test', 'imap', 'INBOX', 4, 'ivo@harbor.example.test', 'Ivo Kalinski', 900, 4),
  ('ada@example.test', 'imap', 'INBOX', 5, 'ivo@harbor.example.test', 'Ivo Kalinski', 800, 5),
  ('ada@example.test', 'imap', 'INBOX', 6, 'ivo@harbor.example.test', 'Ivo Kalinski', 700, 6),
  ('ada@example.test', 'imap', 'INBOX', 7, 'ivo@harbor.example.test', 'Ivo Kalinski', 600, 7),
  ('ada@example.test', 'imap', 'INBOX', 8, 'lin@example.test', 'Lin Mori', 50, 8),
  ('ada@example.test', 'imap', 'INBOX', 9, 'li.wu@example.test', '', 40, 9);
";

fn store() -> (tempfile::TempDir, DiscoveryStore) {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    let key = DbKey::from_bytes([0x5e; 32]);
    Store::open(&path, &key).unwrap().create_list_tables().unwrap();
    let database = Arc::new(Database::new(path, key, Arc::new(Open)));
    database.read(|c| c.execute_batch(PEOPLE)).unwrap();
    (dir, DiscoveryStore::new(database, Arc::new(Frozen)))
}

fn addresses(store: &DiscoveryStore, needle: &str) -> Vec<String> {
    store.senders_like(needle).unwrap().into_iter().map(|s| s.addr.unwrap()).collect()
}

/// A name or an address that starts with what was typed comes first, then a later word of a name, then the rest,
/// each the most often first: "li" is Lin Mori before Mae Lindqvist, who writes more.
#[test]
fn a_name_or_address_starting_with_what_was_typed_comes_first() {
    let (_dir, store) = store();
    assert_eq!(
        addresses(&store, "li"),
        ["lin@example.test", "li.wu@example.test", "mae@guild.example.test", "ivo@harbor.example.test"]
    );
    assert_eq!(addresses(&store, "lind"), ["mae@guild.example.test"]);
    // With nothing typed, the most often and then the latest, as before.
    assert_eq!(
        addresses(&store, ""),
        ["ivo@harbor.example.test", "mae@guild.example.test", "lin@example.test", "li.wu@example.test"]
    );
}
