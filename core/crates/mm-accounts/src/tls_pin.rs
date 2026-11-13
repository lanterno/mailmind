//! A loopback server's certificate, pinned on first use (`mailmind.accounts.domain.tls_pin`).
//!
//! Off this machine every certificate is verified. On loopback it can't be: Proton Bridge serves a certificate it
//! signed itself. So the first certificate an account meets at a loopback endpoint is pinned, by the SHA-256 of its
//! DER bytes, and from then on a connection there that presents any other is closed before a credential is sent.

use sha2::{Digest, Sha256};

const LOOPBACK: [&str; 4] = ["127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"];

/// On this machine: the only place a certificate's checks may be skipped, since nowhere else does a pin stand in.
pub fn is_loopback(host: &str) -> bool {
    LOOPBACK.contains(&mm_kernel::text::strip(host).to_lowercase().as_str())
}

/// `sha256:` and the hex SHA-256 of a certificate's DER bytes.
pub fn fingerprint(der: &[u8]) -> String {
    let digest = Sha256::digest(der);
    let mut out = String::with_capacity(7 + 64);
    out.push_str("sha256:");
    for byte in digest {
        out.push_str(&format!("{byte:02x}"));
    }
    out
}

/// The key a pin is kept under: the host as configured, and the port.
pub fn endpoint(host: &str, port: u16) -> String {
    format!("{}:{port}", mm_kernel::text::strip(host).to_lowercase())
}
