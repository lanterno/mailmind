//! TLS to a mail server, on ring (`imap_connection.tls_context`). Off this machine a server's certificate is verified:
//! a chain to a root the system trusts, for the name the account gives, as the Python app's OpenSSL verifies it. On
//! loopback the chain isn't checked, since Proton Bridge signs its own: the caller checks its pin before a credential
//! is sent. Its signatures still are, so the server proves it holds the certificate's key.

use std::sync::{Arc, OnceLock};

use rustls::client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
use rustls::crypto::{CryptoProvider, verify_tls12_signature, verify_tls13_signature};
use rustls::pki_types::{CertificateDer, ServerName, UnixTime};
use rustls::{ClientConfig, DigitallySignedStruct, RootCertStore, SignatureScheme};

use crate::Lost;

/// Takes any certificate a loopback server presents, for its pin to be checked after the handshake.
#[derive(Debug)]
struct PinnedAfter {
    provider: Arc<CryptoProvider>,
}

impl ServerCertVerifier for PinnedAfter {
    fn verify_server_cert(
        &self,
        _end_entity: &CertificateDer<'_>,
        _intermediates: &[CertificateDer<'_>],
        _server_name: &ServerName<'_>,
        _ocsp_response: &[u8],
        _now: UnixTime,
    ) -> Result<ServerCertVerified, rustls::Error> {
        Ok(ServerCertVerified::assertion())
    }

    fn verify_tls12_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, rustls::Error> {
        verify_tls12_signature(message, cert, dss, &self.provider.signature_verification_algorithms)
    }

    fn verify_tls13_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, rustls::Error> {
        verify_tls13_signature(message, cert, dss, &self.provider.signature_verification_algorithms)
    }

    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        self.provider.signature_verification_algorithms.supported_schemes()
    }
}

/// The TLS a server gets: on this machine, its certificate pinned rather than verified, the pin checked by the caller
/// before a credential is sent; off it, verified on the roots the system trusts. Those are read once, at the first
/// connection that needs them.
pub fn config(loopback: bool) -> Result<Arc<ClientConfig>, Lost> {
    if loopback {
        return pinned_after().map_err(|_| Lost("TlsError"));
    }
    static SYSTEM: OnceLock<Result<Arc<ClientConfig>, Lost>> = OnceLock::new();
    SYSTEM.get_or_init(|| verified(rustls_native_certs::load_native_certs().certs)).clone()
}

/// TLS that verifies a server's certificate on `roots` alone. `config` gives it the system's; a test gives it a
/// root of its own, which signed the certificates its server presents, so it never reaches the system's store.
pub fn verified(roots: Vec<CertificateDer<'static>>) -> Result<Arc<ClientConfig>, Lost> {
    let mut store = RootCertStore::empty();
    let (added, _unreadable) = store.add_parsable_certificates(roots);
    if added == 0 {
        return Err(Lost("NoTrustedRoots"));
    }
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let config = ClientConfig::builder_with_provider(provider)
        .with_safe_default_protocol_versions()
        .map_err(|_| Lost("TlsError"))?
        .with_root_certificates(store)
        .with_no_client_auth();
    Ok(Arc::new(config))
}

fn pinned_after() -> Result<Arc<ClientConfig>, rustls::Error> {
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let config = ClientConfig::builder_with_provider(provider.clone())
        .with_safe_default_protocol_versions()?
        .dangerous()
        .with_custom_certificate_verifier(Arc::new(PinnedAfter { provider }))
        .with_no_client_auth();
    Ok(Arc::new(config))
}
