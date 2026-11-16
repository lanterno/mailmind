//! `sync_health`: when an account's failed sync shows. Values only.

use mm_mailbox::sync_health::{Failing, may_pass, shows};

#[test]
fn trouble_that_may_pass_is_a_connection_or_a_server_busy_for_now_and_never_a_refusal() {
    assert!(may_pass("ConnectionLost", "NETWORK"));
    assert!(may_pass("Timeout", "TIMEOUT"));
    assert!(may_pass("LoginUnavailable", "AUTH"));
    for (class, kind) in [("LoginRefused", "AUTH"), ("TlsPinMismatch", "TLS_PIN"), ("Panic", "UNKNOWN"), ("NO", "IMAP")]
    {
        assert!(!may_pass(class, kind), "{class}");
    }
}

#[test]
fn trouble_that_may_pass_shows_on_the_second_failure_in_a_row_or_three_minutes_on() {
    assert!(!shows(true, 1, 0));
    assert!(!shows(true, 1, 179));
    assert!(shows(true, 1, 180));
    assert!(shows(true, 2, 0));
    assert!(shows(false, 1, 0), "any other shows at once");
}

#[test]
fn a_run_counts_its_failures_from_the_first() {
    let first = Failing::and(None, "ConnectionLost", true, 1_000);
    assert_eq!(first, Failing { rounds: 1, since: 1_000, class: "ConnectionLost", passing: true });
    assert!(!first.shows(1_179) && first.shows(1_180));
    let second = Failing::and(Some(&first), "Timeout", true, 1_060);
    assert_eq!(second, Failing { rounds: 2, since: 1_000, class: "Timeout", passing: true });
    assert!(second.shows(1_060));
}
