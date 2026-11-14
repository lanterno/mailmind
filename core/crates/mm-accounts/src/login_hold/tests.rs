//! The keyed digest a login's id is.

use super::*;

#[test]
fn a_logins_id_is_hmac_sha256() {
    // RFC 4231's second case: a key shorter than a block is the same key with zeros after it.
    let mut key = [0u8; 32];
    key[..4].copy_from_slice(b"Jefe");
    let digest = hmac(&key, b"what do ya want for nothing?");
    let hex: String = digest.iter().map(|b| format!("{b:02x}")).collect();
    assert_eq!(hex, "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843");
}
