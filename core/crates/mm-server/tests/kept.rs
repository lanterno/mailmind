//! Each account's IMAP connection kept on the Rust server (#5), against a server on loopback that writes down what
//! each connection was asked: the jobs on an account's gate log in once between them, whatever use case runs them,
//! and every session holding no gate logs in and out as before; a connection the server ended while it sat is replaced
//! with no job failing; a wake closes them; an account's change or removal, and a quit, log its connection out; a
//! folder made, renamed or deleted shares the login, and one renamed that the connection had selected logs it out; and
//! how long one may serve is its provider's.

#![allow(clippy::unwrap_used)]

mod stand_in;

use mm_accounts::account::Account;
use mm_mailbox::gates::{Priority, Wait, on_gate};
use mm_mailbox::kept::Keeping;
use mm_mailbox::session::MailServers;
use mm_server::kept_wiring::keeping_of;
use serde_json::json;
use stand_in::{ADA, BEA, Imap, World, note, until, with_pdf, world};

/// Ada's and Bea's Inbox, three notes each.
fn inboxes() -> Imap {
    let imap = Imap::start();
    for email in [ADA, BEA] {
        imap.add(email, "INBOX", (1..=3).map(|uid| (uid, note(uid))));
        imap.add(email, "Archive", []);
    }
    imap
}

/// A job on `account`'s gate that reads message `uid` of its Inbox, as opening a message does.
fn job(w: &World, account: &str, uid: u32) {
    on_gate(&*w.app.gates, account, Priority::Interactive, Wait::Ever, || {
        let mut s = w.app.kept.open(account, 60).unwrap();
        s.select("INBOX", true).unwrap();
        assert!(s.body(uid).unwrap().is_some());
        s.logout();
    })
    .unwrap();
}

/// The connection each LOGIN of `account`'s came on, in order.
fn logins(w: &World, account: &str) -> Vec<usize> {
    w.imap.sent_on(account, "LOGIN").into_iter().map(|(connection, _)| connection).collect()
}

#[test]
fn the_jobs_on_an_accounts_gate_log_in_once_between_them() {
    let w = world(inboxes(), &[ADA, BEA], &[]);
    for uid in [1, 2, 3] {
        job(&w, ADA, uid);
        job(&w, BEA, uid);
    }
    assert_eq!(logins(&w, ADA).len(), 1);
    assert_eq!(logins(&w, BEA).len(), 1);
    // Each later job asked first whether the connection still answers, and none logged out.
    assert_eq!(w.imap.sent(ADA, "NOOP").len(), 2);
    assert!(w.imap.sent(ADA, "LOGOUT").is_empty());
    assert_eq!(w.imap.open(), 2);
}

#[test]
fn messages_opened_and_one_fetched_ahead_share_one_login() {
    // Each too large for the opening a sync reads to hold it whole, so each is fetched as it is opened.
    let imap = Imap::start();
    imap.add(ADA, "INBOX", (1..=3).map(|uid| (uid, with_pdf(uid, "The plans, attached.", 100))));
    imap.add(ADA, "Archive", []);
    let w = world(imap, &[ADA], &[]);
    w.app.syncing.sync_once();
    let synced = logins(&w, ADA).len();
    // The sync holds no gate: it logged in and out.
    assert_eq!(w.imap.sent(ADA, "LOGOUT").len(), synced);
    for uid in [1, 2] {
        let opened = w.app.reading.bodies.message_body(w.pk(ADA, "INBOX", uid), false).unwrap();
        assert!(opened["ok"].as_bool().unwrap(), "{opened}");
    }
    let ahead = w.app.reading.bodies.message_body(w.pk(ADA, "INBOX", 3), true).unwrap();
    assert_eq!(ahead["prefetched"], json!(true));
    assert_eq!(logins(&w, ADA).len(), synced + 1);
    let fetched: Vec<usize> = w
        .imap
        .sent_on(ADA, "UID FETCH")
        .into_iter()
        .filter(|(_, c)| c.ends_with("BODY.PEEK[])"))
        .map(|(on, _)| on)
        .collect();
    assert_eq!(fetched.len(), 3);
    assert!(fetched.iter().all(|on| *on == fetched[0]), "{fetched:?}");
    assert_eq!(w.imap.sent(ADA, "LOGOUT").len(), synced);
}

#[test]
fn a_session_holding_no_gate_logs_in_and_out_as_before() {
    let w = world(inboxes(), &[ADA], &[]);
    for _ in 0..2 {
        let mut s = w.app.kept.open(ADA, 60).unwrap();
        s.select("INBOX", true).unwrap();
        s.logout();
    }
    assert_eq!(logins(&w, ADA).len(), 2);
    assert_eq!(w.imap.sent(ADA, "LOGOUT").len(), 2);
    assert!(w.imap.sent(ADA, "NOOP").is_empty());
}

#[test]
fn a_connection_the_server_ended_while_it_sat_is_replaced_and_the_job_goes_on() {
    let w = world(inboxes(), &[ADA], &[]);
    job(&w, ADA, 1);
    // The server's idle timeout, or the network gone and back.
    w.imap.end_every_connection();
    job(&w, ADA, 2);
    assert_eq!(logins(&w, ADA), [1, 2]);
    assert_eq!(w.app.kept.stats().counts.validate_fail, 1);
}

#[test]
fn a_wake_closes_each_kept_connection_and_the_next_job_logs_in_afresh() {
    let w = world(inboxes(), &[ADA, BEA], &[]);
    job(&w, ADA, 1);
    job(&w, BEA, 1);
    w.app.kept.woke();
    until(|| w.imap.open() == 0);
    // Closed without a word: the server may be gone.
    assert!(w.imap.sent(ADA, "LOGOUT").is_empty());
    let mark = w.imap.mark();
    job(&w, ADA, 2);
    assert_eq!(w.imap.since(mark, ADA)[0], "LOGIN", "no NOOP on a connection a wake closed");
}

#[test]
fn an_account_removed_has_its_connection_logged_out_and_the_others_keep_theirs() {
    let w = world(inboxes(), &[ADA, BEA], &[]);
    job(&w, ADA, 1);
    job(&w, BEA, 1);
    let asked = json!({"email": ADA}).as_object().unwrap().clone();
    w.app.connections.delete(&asked).unwrap();
    assert_eq!(w.imap.sent(ADA, "LOGOUT").len(), 1);
    until(|| w.imap.open() == 1);
    job(&w, BEA, 2);
    assert_eq!(logins(&w, BEA).len(), 1);
}

#[test]
fn an_account_removed_with_its_mail_has_its_connection_logged_out_and_keeps_none_after() {
    let w = world(inboxes(), &[ADA, BEA], &[]);
    w.app.syncing.sync_once();
    job(&w, ADA, 1);
    job(&w, BEA, 1);
    let kept_on = *logins(&w, ADA).last().unwrap();
    // Its mail goes on its gate, after its syncs were told to stop and its connection let go.
    let asked = json!({"email": ADA, "delete_mail": true}).as_object().unwrap().clone();
    let removed = w.app.connections.delete(&asked).unwrap();
    assert_eq!(removed["mail_deleted"], json!(3));
    assert!(w.imap.sent_on(ADA, "LOGOUT").iter().any(|(on, _)| *on == kept_on));
    until(|| w.imap.open() == 1);
    assert_eq!(w.app.kept.stats().slots.len(), 1, "Bea's alone");
    job(&w, BEA, 2);
    assert_eq!(logins(&w, BEA).len(), 2, "the sync's, then the one kept");
}

#[test]
fn an_account_changed_logs_in_again_as_it_now_is() {
    let w = world(inboxes(), &[ADA], &[]);
    job(&w, ADA, 1);
    let asked = json!({"email": ADA, "alias": "Ada at home"}).as_object().unwrap().clone();
    w.app.connections.edit(&asked).unwrap();
    assert_eq!(w.imap.sent(ADA, "LOGOUT").len(), 1);
    job(&w, ADA, 2);
    assert_eq!(logins(&w, ADA), [1, 2]);
}

#[test]
fn a_quit_logs_each_kept_connection_out() {
    let w = world(inboxes(), &[ADA, BEA], &[]);
    job(&w, ADA, 1);
    job(&w, BEA, 1);
    w.app.kept.close();
    assert_eq!((w.imap.sent(ADA, "LOGOUT").len(), w.imap.sent(BEA, "LOGOUT").len()), (1, 1));
    until(|| w.imap.open() == 0);
    // Nothing is kept after.
    job(&w, ADA, 2);
    assert_eq!(w.imap.sent(ADA, "LOGOUT").len(), 2);
}

/// What `account` was asked of folders themselves, with the connection each came on.
fn folder_changes(w: &World, account: &str) -> Vec<String> {
    let changed = ["CREATE", "RENAME", "DELETE"].iter().flat_map(|verb| w.imap.sent_on(account, verb));
    let mut changed: Vec<(usize, String)> = changed.collect();
    changed.sort();
    changed.into_iter().map(|(on, command)| format!("{on} {command}")).collect()
}

#[test]
fn a_folder_made_renamed_and_deleted_shares_the_jobs_login() {
    let w = world(inboxes(), &[ADA], &[]);
    job(&w, ADA, 1);
    let made = w.app.folder_changes.create(Some(ADA), Some("Plans")).unwrap();
    assert_eq!(made["folder"], json!("Plans"));
    w.app.folder_changes.rename(Some(ADA), Some("Plans"), Some("Projects")).unwrap();
    w.app.folder_changes.delete(Some(ADA), Some("Projects"), false).unwrap();
    job(&w, ADA, 2);
    assert_eq!(logins(&w, ADA), [1]);
    assert_eq!(
        folder_changes(&w, ADA),
        ["1 CREATE \"Plans\"", "1 DELETE \"Projects\"", "1 RENAME \"Plans\" \"Projects\""]
    );
    assert!(w.imap.sent(ADA, "LOGOUT").is_empty());
}

#[test]
fn a_folder_renamed_that_the_kept_connection_has_selected_logs_it_out_and_the_next_job_logs_in_afresh() {
    let imap = inboxes();
    imap.add(ADA, "Receipts", [(1, note(1))]);
    let w = world(imap, &[ADA], &[]);
    // A message read in Receipts, which the server holds selected on the connection after.
    on_gate(&*w.app.gates, ADA, Priority::Interactive, Wait::Ever, || {
        let mut s = w.app.kept.open(ADA, 60).unwrap();
        s.select("Receipts", true).unwrap();
        assert!(s.body(1).unwrap().is_some());
        s.logout();
    })
    .unwrap();
    let renamed = w.app.folder_changes.rename(Some(ADA), Some("Receipts"), Some("Paid")).unwrap();
    assert_eq!(renamed["folder"], json!("Paid"));
    assert_eq!(folder_changes(&w, ADA), ["1 RENAME \"Receipts\" \"Paid\""]);
    assert_eq!(w.imap.sent(ADA, "LOGOUT").len(), 1);
    until(|| w.imap.open() == 0);
    job(&w, ADA, 1);
    assert_eq!(logins(&w, ADA), [1, 2]);
}

#[test]
fn with_one_gate_for_every_account_each_still_keeps_its_own() {
    let w = world(inboxes(), &[ADA, BEA], &[("MAILMIND_WORKERS", "one")]);
    for uid in [1, 2] {
        job(&w, ADA, uid);
        job(&w, BEA, uid);
    }
    assert_eq!((logins(&w, ADA).len(), logins(&w, BEA).len()), (1, 1));
}

#[test]
fn proton_bridge_and_a_server_like_it_on_this_machine_recycle_sooner() {
    let account = |entry: serde_json::Value| keeping_of(&Account::from_entry(&entry).unwrap());
    let bridge = json!({"email": ADA, "type": "proton"});
    assert_eq!(account(bridge), Keeping::BRIDGE);
    let local_starttls = json!({"email": ADA, "type": "imap", "host": "127.0.0.1", "port": 1143, "starttls": true});
    assert_eq!(account(local_starttls), Keeping::BRIDGE);
    let local_tls = json!({"email": ADA, "type": "imap", "host": "127.0.0.1", "port": 993, "starttls": false});
    assert_eq!(account(local_tls), Keeping::USUAL);
    let remote = json!({"email": ADA, "type": "imap", "host": "imap.example.test", "port": 143, "starttls": true});
    assert_eq!(account(remote), Keeping::USUAL);
    assert_eq!(account(json!({"email": ADA, "type": "gmail"})), Keeping::USUAL);
}
