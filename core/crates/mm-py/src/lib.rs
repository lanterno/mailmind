//! The leaves the core shares with the Python app, as the Python module `mailmind_leaves`.
//!
//! Each is a pure function the Python app used to compute itself, now computed once, here, for every client. The
//! Python app calls these while it is still the server, and its tests hold each to the answers it gave. This crate
//! goes when the Rust server replaces the Python one; nothing else in the core knows it exists.
//!
//! Each leaf adds its functions in a file of its own, and `mailmind_leaves.pyi` says what each takes and gives.
//! What crosses is plain: strings, numbers, tuples, lists and dicts. The Python module a leaf replaces keeps its
//! own types and builds them from these.

use pyo3::prelude::*;

mod classifier;
mod query;
mod threading;

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

#[pymodule]
fn mailmind_leaves(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(identity_of, m)?)?;
    query::register(m)?;
    classifier::register(m)?;
    threading::register(m)?;
    Ok(())
}
