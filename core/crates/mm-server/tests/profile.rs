//! Which profile the environment selects, a full process or one that only reads, and the background work's pace.

use mm_server::environment::Environment;

fn read_only(pairs: &[(&str, &str)]) -> bool {
    Environment::from_pairs(pairs.iter().copied()).read_only()
}

#[test]
fn a_process_is_full_unless_told_otherwise() {
    assert!(!read_only(&[]));
    assert!(!read_only(&[("MAILMIND_PROFILE", "")]));
    assert!(!read_only(&[("MAILMIND_PROFILE", " Full ")]));
    assert!(!read_only(&[("MAILMIND_NOSYNC", "0"), ("MAILMIND_PROFILE", "full")]));
}

#[test]
fn reconcile_goes_round_every_half_hour_unless_told_otherwise() {
    let secs = |pairs: &[(&str, &str)]| Environment::from_pairs(pairs.iter().copied()).reconcile_secs();
    assert_eq!(secs(&[]), 1800);
    assert_eq!(secs(&[("MAILMIND_RECONCILE_SECS", " ")]), 1800);
    assert_eq!(secs(&[("MAILMIND_RECONCILE_SECS", "soon")]), 1800);
    assert_eq!(secs(&[("MAILMIND_RECONCILE_SECS", " 600 ")]), 600);
    assert_eq!(secs(&[("MAILMIND_RECONCILE_SECS", "0")]), 0);
}

#[test]
fn the_expiry_rules_go_round_every_hour_unless_told_otherwise() {
    let secs = |pairs: &[(&str, &str)]| Environment::from_pairs(pairs.iter().copied()).expire_secs();
    assert_eq!(secs(&[]), 3600);
    assert_eq!(secs(&[("MAILMIND_EXPIRE_SECS", "")]), 3600);
    assert_eq!(secs(&[("MAILMIND_EXPIRE_SECS", "hourly")]), 3600);
    assert_eq!(secs(&[("MAILMIND_EXPIRE_SECS", " 900 ")]), 900);
    assert_eq!(secs(&[("MAILMIND_EXPIRE_SECS", "0")]), 0);
    assert_eq!(secs(&[("MAILMIND_EXPIRE_SECS", "-5")]), -5);
}

#[test]
fn read_only_wins_and_a_name_that_is_no_profile_fails_safe() {
    assert!(read_only(&[("MAILMIND_PROFILE", "read-only")]));
    assert!(read_only(&[("MAILMIND_PROFILE", "readonly")]));
    assert!(read_only(&[("MAILMIND_NOSYNC", "1"), ("MAILMIND_PROFILE", "full")]));
    assert!(read_only(&[("MAILMIND_NOSYNC", "maybe")]));
}
