//! Which profile the environment selects: a full process, or one that only reads.

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
fn read_only_wins_and_a_name_that_is_no_profile_fails_safe() {
    assert!(read_only(&[("MAILMIND_PROFILE", "read-only")]));
    assert!(read_only(&[("MAILMIND_PROFILE", "readonly")]));
    assert!(read_only(&[("MAILMIND_NOSYNC", "1"), ("MAILMIND_PROFILE", "full")]));
    assert!(read_only(&[("MAILMIND_NOSYNC", "maybe")]));
}
