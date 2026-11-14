use std::fmt;

use mm_kernel::Refusal;
use mm_kernel::seams::Secret;
use zeroize::Zeroizing;

/// The key that opens a database: 32 bytes, which the platform's key store hands over. It never prints, and its
/// bytes are wiped when it is dropped.
pub struct DbKey(Zeroizing<[u8; 32]>);

impl DbKey {
    pub const SIZE: usize = 32;

    pub fn from_bytes(bytes: [u8; 32]) -> DbKey {
        DbKey(Zeroizing::new(bytes))
    }

    /// A key written as 64 hex digits, as `MAILMIND_DB_KEY` holds it.
    pub fn from_hex(text: &str) -> Result<DbKey, Refusal> {
        let text = text.trim().as_bytes();
        if text.len() != DbKey::SIZE * 2 {
            return Err(Refusal::invalid("a database key is 64 hex digits"));
        }
        let mut key = DbKey(Zeroizing::new([0u8; 32]));
        let (pairs, _) = text.as_chunks::<2>();
        for (byte, [high, low]) in key.0.iter_mut().zip(pairs) {
            *byte = (digit(*high)? << 4) | digit(*low)?;
        }
        Ok(key)
    }

    /// A new key, its bytes written in place by `fill` (the system's random source), so no copy of them is left
    /// behind.
    pub fn filled<E>(fill: impl FnOnce(&mut [u8]) -> Result<(), E>) -> Result<DbKey, E> {
        let mut key = DbKey(Zeroizing::new([0u8; 32]));
        fill(&mut key.0[..])?;
        Ok(key)
    }

    /// The key as 64 hex digits, as `MAILMIND_DB_KEY` and the Keychain hold it.
    pub fn hex(&self) -> Secret {
        let mut text = String::with_capacity(DbKey::SIZE * 2);
        for byte in self.0.iter() {
            text.push(hex(byte >> 4));
            text.push(hex(byte & 0x0f));
        }
        Secret::new(text)
    }

    /// The statement that keys a connection: the raw key, so SQLCipher derives nothing from it.
    pub(crate) fn pragma(&self) -> Zeroizing<String> {
        let mut text = Zeroizing::new(String::with_capacity(80));
        text.push_str("PRAGMA key = \"x'");
        for byte in self.0.iter() {
            text.push(hex(byte >> 4));
            text.push(hex(byte & 0x0f));
        }
        text.push_str("'\";");
        text
    }
}

impl fmt::Debug for DbKey {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("DbKey(..)")
    }
}

fn digit(c: u8) -> Result<u8, Refusal> {
    match c {
        b'0'..=b'9' => Ok(c - b'0'),
        b'a'..=b'f' => Ok(c - b'a' + 10),
        b'A'..=b'F' => Ok(c - b'A' + 10),
        _ => Err(Refusal::invalid("a database key is 64 hex digits")),
    }
}

fn hex(n: u8) -> char {
    char::from(b"0123456789abcdef"[usize::from(n)])
}
