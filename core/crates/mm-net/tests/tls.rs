//! TLS to a mail server: verified off this machine, pinned on it. Verified TLS is tested on a loopback server whose
//! certificates the test makes, signed by a root it trusts through `tls::verified`, and named for a host the
//! connection never looks up: the system's roots are never asked.

#![allow(clippy::unwrap_used)]

use std::io::{BufRead, BufReader, Write};
use std::net::TcpListener;
use std::sync::Arc;
use std::thread::JoinHandle;
use std::time::Duration;

use mm_net::{Lost, tls};
use rcgen::{
    BasicConstraints, Certificate, CertificateParams, ExtendedKeyUsagePurpose, IsCa, Issuer, KeyPair, KeyUsagePurpose,
    date_time_ymd,
};
use rustls::pki_types::{CertificateDer, PrivateKeyDer, PrivatePkcs8KeyDer};
use rustls::{ServerConfig, ServerConnection, StreamOwned};

const QUICK: Duration = Duration::from_secs(5);

/// A root the test may trust, and its key, to sign with.
struct Root {
    params: CertificateParams,
    key: KeyPair,
    cert: Certificate,
}

fn root() -> Root {
    let key = KeyPair::generate().unwrap();
    let mut params = CertificateParams::new(Vec::<String>::new()).unwrap();
    params.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
    params.key_usages = vec![KeyUsagePurpose::KeyCertSign, KeyUsagePurpose::CrlSign];
    let cert = params.self_signed(&key).unwrap();
    Root { params, key, cert }
}

/// A server's certificate for `names`, signed by `root`, and its key.
fn signed(root: &Root, names: &[&str], params: impl FnOnce(&mut CertificateParams)) -> (Certificate, KeyPair) {
    let key = KeyPair::generate().unwrap();
    let mut wanted = CertificateParams::new(names.iter().map(|n| (*n).to_owned()).collect::<Vec<_>>()).unwrap();
    wanted.extended_key_usages = vec![ExtendedKeyUsagePurpose::ServerAuth];
    params(&mut wanted);
    let cert = wanted.signed_by(&key, &Issuer::from_params(&root.params, &root.key)).unwrap();
    (cert, key)
}

/// A server on loopback presenting `cert`: once the handshake is done it says hello, and leaves.
fn serving(cert: &Certificate, key: &KeyPair) -> (u16, JoinHandle<()>) {
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let chain = vec![cert.der().clone()];
    let key = PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(key.serialize_der()));
    let config = ServerConfig::builder_with_provider(provider)
        .with_safe_default_protocol_versions()
        .unwrap()
        .with_no_client_auth()
        .with_single_cert(chain, key)
        .unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let serving = std::thread::spawn(move || {
        let (tcp, _) = listener.accept().unwrap();
        tcp.set_read_timeout(Some(QUICK)).unwrap();
        let mut tls = StreamOwned::new(ServerConnection::new(Arc::new(config)).unwrap(), tcp);
        // A client that refused the certificate leaves here, before anything is said.
        let _ = tls.write_all(b"hello\r\n").and_then(|()| tls.flush());
    });
    (port, serving)
}

/// What the server at `port` says, reached as `name` over `config`; or why it wasn't.
fn heard(config: Arc<rustls::ClientConfig>, name: &str, port: u16) -> Result<String, Lost> {
    let tcp = mm_net::connect("127.0.0.1", port, QUICK)?;
    let stream = mm_net::handshake(config, mm_net::server_name(name)?, tcp)?;
    let mut line = String::new();
    BufReader::new(stream).read_line(&mut line).map_err(|e| mm_net::lost(&e))?;
    Ok(line)
}

fn trusting(root: &Root) -> Arc<rustls::ClientConfig> {
    tls::verified(vec![root.cert.der().clone()]).unwrap()
}

#[test]
fn a_certificate_a_trusted_root_signed_for_the_servers_name_is_verified() {
    let root = root();
    let (cert, key) = signed(&root, &["imap.example.test"], |_| {});
    let (port, serving) = serving(&cert, &key);
    assert_eq!(heard(trusting(&root), "imap.example.test", port).unwrap(), "hello\r\n");
    serving.join().unwrap();
}

#[test]
fn a_certificate_no_trusted_root_signed_is_refused() {
    let (trusted, other) = (root(), root());
    let (cert, key) = signed(&other, &["imap.example.test"], |_| {});
    let (port, serving) = serving(&cert, &key);
    assert_eq!(heard(trusting(&trusted), "imap.example.test", port), Err(Lost("CertificateNotVerified")));
    serving.join().unwrap();
}

#[test]
fn a_certificate_for_another_name_is_refused() {
    let root = root();
    let (cert, key) = signed(&root, &["mail.example.test"], |_| {});
    let (port, serving) = serving(&cert, &key);
    assert_eq!(heard(trusting(&root), "imap.example.test", port), Err(Lost("CertificateNotVerified")));
    serving.join().unwrap();
}

#[test]
fn a_certificate_past_its_time_is_refused() {
    let root = root();
    let (cert, key) = signed(&root, &["imap.example.test"], |p| {
        p.not_before = date_time_ymd(2019, 1, 1);
        p.not_after = date_time_ymd(2020, 1, 1);
    });
    let (port, serving) = serving(&cert, &key);
    assert_eq!(heard(trusting(&root), "imap.example.test", port), Err(Lost("CertificateNotVerified")));
    serving.join().unwrap();
}

#[test]
fn with_no_root_to_trust_nothing_can_be_verified() {
    assert_eq!(tls::verified(Vec::new()).unwrap_err(), Lost("NoTrustedRoots"));
    assert_eq!(
        tls::verified(vec![CertificateDer::from(b"not a certificate".to_vec())]).unwrap_err(),
        Lost("NoTrustedRoots")
    );
}

#[test]
fn a_loopback_server_is_reached_whoever_signed_it_and_its_certificate_handed_back_for_its_pin() {
    let nobody = root();
    let (cert, key) = signed(&nobody, &["127.0.0.1"], |_| {});
    let (port, serving) = serving(&cert, &key);
    let tcp = mm_net::connect("127.0.0.1", port, QUICK).unwrap();
    let stream = mm_net::handshake(tls::config(true).unwrap(), mm_net::server_name("127.0.0.1").unwrap(), tcp).unwrap();
    assert_eq!(stream.peer_certificate().as_deref(), Some(cert.der().as_ref()));
    let mut line = String::new();
    BufReader::new(stream).read_line(&mut line).unwrap();
    assert_eq!(line, "hello\r\n");
    serving.join().unwrap();
}
