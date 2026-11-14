//! What `/api/debug/health` counts of the durable queues, on a database the core makes: the rows of the outbox and of
//! the change queue in each state (`DurableQueue.counts`).

#![allow(clippy::unwrap_used)]

use std::collections::BTreeMap;
use std::path::Path;
use std::sync::Arc;

use mm_compose::ports::StoredOutbox;
use mm_kernel::seams::{Clock, Moment, Outside, Tripped};
use mm_mailbox::command_ports;
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

/// Sends in each state, as the outbox keeps them.
const OUTBOX: &str = "
INSERT INTO outbox (account, idempotency_key, message_id, envelope, recipients, raw, state, created_at) VALUES
  ('ada@example.test', 'k1', '<1@example.test>', '[]', '{}', x'00', 'queued', '2026-11-02T12:00:00+00:00'),
  ('ada@example.test', 'k2', '<2@example.test>', '[]', '{}', x'00', 'queued', '2026-11-02T12:00:00+00:00'),
  ('ada@example.test', 'k3', '<3@example.test>', '[]', '{}', x'00', 'sending', '2026-11-02T12:00:00+00:00'),
  ('ada@example.test', 'k4', '<4@example.test>', '[]', '{}', x'00', 'sent', '2026-11-02T12:00:00+00:00'),
  ('ada@example.test', 'k5', '<5@example.test>', '[]', '{}', x'00', 'error', '2026-11-02T12:00:00+00:00');
";

/// Changes in each state, as the change queue keeps them: done ones too.
const COMMANDS: &str = "
INSERT INTO mail_commands (account, idempotency_key, kind, message_id, src_folder, params, state, applied, created_at)
VALUES
  ('ada@example.test', 'c1', 'archive', '<1@example.test>', 'INBOX', '{}', 'queued', 0, '2026-11-02T12:00:00+00:00'),
  ('ada@example.test', 'c2', 'trash', '<2@example.test>', 'INBOX', '{}', 'running', 0, '2026-11-02T12:00:00+00:00'),
  ('ada@example.test', 'c3', 'trash', '<3@example.test>', 'INBOX', '{}', 'done', 1, '2026-11-02T12:00:00+00:00'),
  ('ada@example.test', 'c4', 'flag', '<4@example.test>', 'INBOX', '{}', 'done', 1, '2026-11-02T12:00:00+00:00'),
  ('ada@example.test', 'c5', 'move', '<5@example.test>', 'INBOX', '{}', 'error', 0, '2026-11-02T12:00:00+00:00');
";

fn counted(pairs: &[(&str, i64)]) -> BTreeMap<String, i64> {
    pairs.iter().map(|(state, n)| ((*state).to_owned(), *n)).collect()
}

#[test]
fn the_outbox_and_the_change_queue_count_their_rows_in_each_state() {
    let dir = tempfile::tempdir().unwrap();
    let database = Database::new(dir.path().join("mailmind.db"), DbKey::from_bytes([0x5e; 32]), Arc::new(Open));
    database.ready(&Frozen).unwrap();
    assert_eq!(StoredOutbox::counts(&database).unwrap(), BTreeMap::new());
    assert_eq!(command_ports::read(&database, |tx| tx.counts()).unwrap(), BTreeMap::new());
    database.write(|c| c.execute_batch(&format!("{OUTBOX}{COMMANDS}"))).unwrap();
    assert_eq!(
        StoredOutbox::counts(&database).unwrap(),
        counted(&[("error", 1), ("queued", 2), ("sending", 1), ("sent", 1)])
    );
    assert_eq!(
        command_ports::read(&database, |tx| tx.counts()).unwrap(),
        counted(&[("done", 2), ("error", 1), ("queued", 1), ("running", 1)])
    );
}
