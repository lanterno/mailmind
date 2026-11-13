//! What a reconcile may take away and what a sync's failure is called (`tests/unit/mailbox/test_prune.py`'s rules,
//! `accounts.domain.health.error_kind`), and what a sync asked again covers. Values in and values out.

use std::collections::BTreeSet;

use mm_kernel::errors::Fault;
use mm_mailbox::ingest::SyncError;
use mm_mailbox::reconcile::{Pruned, empty_search, renumbered};
use mm_mailbox::session::ServerError;
use mm_mailbox::syncing::{asked_again, error_kind};
use serde_json::json;

#[test]
fn a_folder_the_server_renumbered_is_never_pruned() {
    assert_eq!(renumbered(Some(9), Some(7)).as_deref(), Some("UIDVALIDITY changed (7->9) \u{2014} prune skipped"));
    for (server, stored) in
        [(Some(7), Some(7)), (None, Some(7)), (Some(7), None), (Some(0), Some(7)), (Some(9), Some(0))]
    {
        assert_eq!(renumbered(server, stored), None, "{server:?} {stored:?}");
    }
}

#[test]
fn an_empty_search_against_mail_stored_prunes_nothing_unless_forced() {
    assert_eq!(empty_search(0, 3, false).as_deref(), Some("empty SEARCH against non-empty store \u{2014} skipped"));
    assert_eq!(empty_search(0, 3, true), None);
    assert_eq!(empty_search(0, 0, false), None);
    assert_eq!(empty_search(2, 3, false), None);
}

#[test]
fn an_accounts_result_reads_as_the_answer_shows_it() {
    let pruned = Pruned {
        account: "ada@example.test".into(),
        live: 16,
        stored: 17,
        ghosts: 1,
        pruned: 1,
        folders: 1,
        error: None,
    };
    assert_eq!(
        pruned.to_json(),
        json!({"account": "ada@example.test", "live": 16, "stored": 17, "ghosts": 1, "pruned": 1, "error": null,
               "folders": 1})
    );
}

#[test]
fn a_syncs_failure_is_called_by_its_class_alone() {
    let unreachable = |class| SyncError::Server(ServerError::Unreachable(class));
    assert_eq!(error_kind(unreachable("TLSPinMismatch")), "TLS_PIN");
    assert_eq!(error_kind(unreachable("Timeout")), "TIMEOUT");
    assert_eq!(error_kind(SyncError::Server(ServerError::Refused("LoginRefused"))), "AUTH");
    assert_eq!(error_kind(unreachable("ConnectionRefused")), "NETWORK");
    assert_eq!(error_kind(unreachable("NameNotResolved")), "NETWORK");
    assert_eq!(error_kind(unreachable("TlsError")), "NETWORK");
    assert_eq!(error_kind(SyncError::Server(ServerError::Refused("BadMailbox"))), "IMAP");
    assert_eq!(error_kind(SyncError::Store(Fault::new("StorageError"))), "UNKNOWN");
    assert_eq!(error_kind(SyncError::UnknownAccount), "UNKNOWN");
}

#[test]
fn a_sync_asked_again_covers_what_each_ask_named_and_every_account_wins() {
    let named = |emails: &[&str]| Some(emails.iter().map(|e| (*e).to_owned()).collect::<BTreeSet<String>>());
    assert_eq!(asked_again(&[named(&["a"]), named(&["b", "a"])]), named(&["a", "b"]));
    assert_eq!(asked_again(&[named(&["a"]), None]), None);
}
