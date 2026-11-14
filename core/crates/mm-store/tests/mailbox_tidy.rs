//! What a run of Tidy reads of the change queue's log, on a database the core makes (`command_log`'s
//! `command_ran_under` and `archives_undone`): whether a run went under today's group, and each archive Undo took back.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::Arc;

use mm_kernel::seams::{Clock, Moment, Outside, Tripped};
use mm_mailbox::tidy::{TakenBack, TidyLog};
use mm_store::{Database, DbKey};

#[derive(Debug)]
struct Frozen;

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000)
    }
}

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

/// A run's archive, one of them taken back, and a person's own trash taken back.
const LOG: &str = "
INSERT INTO mail_commands (account, idempotency_key, kind, message_id, src_folder, dest, params, created_at) VALUES
  ('ada@example.test', 'archive:ada@example.test:<m1@example.test>:tidy-2026-11-02', 'archive', '<m1@example.test>',
   'INBOX', 'Archive', '{\"pk\": 1}', '2026-11-02T06:00:00+00:00'),
  ('ada@example.test', 'archive:ada@example.test:<m2@example.test>:tidy-2026-11-02', 'archive', '<m2@example.test>',
   'INBOX', 'Archive', '{\"pk\": 2, \"undone\": \"2026-11-02T07:00:00+00:00\"}', '2026-11-02T06:00:00+00:00'),
  ('bea@example.test', 'archive:bea@example.test::r9', 'archive', NULL,
   'INBOX', 'Archive', '{\"undone\": true}', '2026-11-02T06:00:00+00:00'),
  ('ada@example.test', 'trash:ada@example.test:<m3@example.test>:r1', 'trash', '<m3@example.test>',
   'INBOX', 'Trash', '{\"undone\": \"2026-11-02T07:00:00+00:00\"}', '2026-11-02T06:00:00+00:00');
";

fn database(dir: &Path) -> Database {
    let database = Database::new(dir.join("mailmind.db"), DbKey::from_bytes([0x5e; 32]), Arc::new(Open));
    database.ready(&Frozen).unwrap();
    database
}

#[test]
fn a_day_has_run_once_a_command_went_under_its_group() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path());
    assert_eq!(database.ran_under("tidy-2026-11-02"), Ok(false));
    database.write(|c| c.execute_batch(LOG)).unwrap();
    assert_eq!(database.ran_under("tidy-2026-11-02"), Ok(true));
    assert_eq!(database.ran_under("tidy-2026-11-01"), Ok(false));
    // The group is the request's last part, whole.
    assert_eq!(database.ran_under("2026-11-02"), Ok(false));
}

#[test]
fn what_undo_took_back_is_each_archive_it_reversed() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path());
    assert_eq!(database.archives_undone(), Ok(TakenBack::new()));
    database.write(|c| c.execute_batch(LOG)).unwrap();
    assert_eq!(
        database.archives_undone(),
        Ok(TakenBack::from([
            ("ada@example.test".to_owned(), Some("<m2@example.test>".to_owned())),
            ("bea@example.test".to_owned(), None),
        ]))
    );
}
