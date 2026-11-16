//! What the server's tests that log in share: a loopback server's TLS, and an SMTP server on loopback that lets any
//! login in, where a test of an account logs in once its IMAP server has.

#![allow(clippy::unwrap_used, dead_code)]

use std::io::{BufRead, BufReader, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::Arc;

use rcgen::{CertificateParams, KeyPair};
use rustls::pki_types::{PrivateKeyDer, PrivatePkcs8KeyDer};
use rustls::{ServerConfig, ServerConnection, StreamOwned};

/// A loopback server's TLS: a certificate of its own for 127.0.0.1.
pub fn tls() -> Arc<ServerConfig> {
    let key = KeyPair::generate().unwrap();
    let cert = CertificateParams::new(vec!["127.0.0.1".to_owned()]).unwrap().self_signed(&key).unwrap();
    let config = ServerConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
        .with_safe_default_protocol_versions()
        .unwrap()
        .with_no_client_auth()
        .with_single_cert(vec![cert.der().clone()], PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(key.serialize_der())))
        .unwrap();
    Arc::new(config)
}

/// The port of an SMTP server on loopback, over TLS from the first byte (`smtp_starttls` false), that lets any login in.
pub fn smtp() -> u16 {
    let config = tls();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    std::thread::spawn(move || {
        for tcp in listener.incoming() {
            let Ok(tcp) = tcp else { return };
            let tls = StreamOwned::new(ServerConnection::new(config.clone()).unwrap(), tcp);
            std::thread::spawn(move || {
                let _ = let_in(BufReader::new(tls));
            });
        }
    });
    port
}

fn let_in(mut stream: BufReader<StreamOwned<ServerConnection, TcpStream>>) -> std::io::Result<()> {
    stream.get_mut().write_all(b"220 ready\r\n")?;
    stream.get_mut().flush()?;
    let mut line = String::new();
    while stream.read_line(&mut line)? > 0 {
        let command = line.split_whitespace().next().unwrap_or("").to_uppercase();
        let said: &[u8] = match command.as_str() {
            "EHLO" => b"250-fake\r\n250 AUTH PLAIN LOGIN\r\n",
            "AUTH" => b"235 ok\r\n",
            "QUIT" => b"221 bye\r\n",
            _ => b"502 not here\r\n",
        };
        stream.get_mut().write_all(said)?;
        stream.get_mut().flush()?;
        if command == "QUIT" {
            return Ok(());
        }
        line.clear();
    }
    Ok(())
}
