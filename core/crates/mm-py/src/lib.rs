//! The leaves the core shares with the Python app, as the Python module `mailmind_leaves`.
//!
//! Each is a pure function the Python app used to compute itself, now computed once, here, for every client. The
//! Python app calls these while it is still the server, and its tests hold each to the answers it gave. This crate
//! goes when the Rust server replaces the Python one; nothing else in the core knows it exists.

use pyo3::prelude::*;

#[pymodule]
mod mailmind_leaves {
    use pyo3::prelude::*;

    /// A message's identity: its Message-ID without whitespace, or `~` and the SHA-256 of its envelope.
    #[pyfunction]
    #[pyo3(signature = (message_id, from_addr, date_iso, subject))]
    fn identity_of(
        message_id: Option<&str>,
        from_addr: Option<&str>,
        date_iso: Option<&str>,
        subject: Option<&str>,
    ) -> String {
        mm_kernel::identity::of(message_id, from_addr, date_iso, subject)
    }
}
