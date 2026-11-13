//! The Origins this server's own pages send.

pub(super) fn own_origins(port: u16) -> [String; 2] {
    [format!("http://127.0.0.1:{port}"), format!("http://localhost:{port}")]
}

/// Whether `origin`, lowered, is one of them.
pub(super) fn is_own(origin: &[u8], port: u16) -> bool {
    own_origins(port).iter().any(|own| own.as_bytes() == origin)
}
