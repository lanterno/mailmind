//! What makes two rows copies of one message: the same account and the same identity, `message_identity.identity`.
//!
//! A row is one copy in one folder, and a row doesn't last. A move deletes it, and a new UIDVALIDITY renumbers it.
//! The Message-ID outlives both, written without the whitespace a sender can fold into it. A message with none is
//! known by its envelope, which is all two copies of it share.
//!
//! The Python app writes the same identities into the same files (`mailmind.mailbox.domain.identity`), so this
//! answers exactly as it does, down to which characters count as whitespace.

use sha2::{Digest, Sha256};

/// A message's identity: its Message-ID without whitespace, or `~` and the SHA-256 of its envelope when that is
/// empty.
pub fn of(message_id: Option<&str>, from_addr: Option<&str>, date_iso: Option<&str>, subject: Option<&str>) -> String {
    let written: String = message_id.unwrap_or("").chars().filter(|c| !splits(*c)).collect();
    if !written.is_empty() {
        return written;
    }
    let envelope = [from_addr, date_iso, subject].map(|part| part.unwrap_or("")).join("|");
    let mut out = String::with_capacity(1 + 64);
    out.push('~');
    for byte in Sha256::digest(envelope.as_bytes()) {
        out.push(HEX[usize::from(byte >> 4)] as char);
        out.push(HEX[usize::from(byte & 0xf)] as char);
    }
    out
}

const HEX: &[u8; 16] = b"0123456789abcdef";

/// Whether Python's `str.split()` splits at `c`: Unicode's White_Space, which Rust's `is_whitespace` is, and the
/// four information separators U+001C to U+001F, which Python counts as well.
fn splits(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}
