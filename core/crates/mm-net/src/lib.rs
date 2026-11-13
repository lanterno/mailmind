//! The network every mail protocol shares (`mm-imap`'s, and the protocols to come): a TCP connection with its time
//! limits, TLS on it from the first byte or after the protocol's STARTTLS (`tls`: verified off this machine, pinned
//! by the caller on it), and the stream a protocol reads and writes.
//!
//! What goes wrong is named by its class alone (`Lost`), never by what a server said. Reaching a host is asked of
//! `Outside` by the caller, before `connect`.

pub mod tls;

use std::io::{self, Read, Write};
use std::net::{Shutdown, TcpStream, ToSocketAddrs};
use std::sync::Arc;
use std::time::Duration;

use rustls::pki_types::ServerName;
use rustls::{ClientConfig, ClientConnection, StreamOwned};

/// Why a connection didn't go or was lost, by its class: the Python app's `UNREACHABLE`, where asking again later may
/// help.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Lost(pub &'static str);

/// What an I/O error on a connection is called.
pub fn lost(e: &io::Error) -> Lost {
    Lost(match e.kind() {
        io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock => "Timeout",
        io::ErrorKind::ConnectionRefused => "ConnectionRefused",
        io::ErrorKind::InvalidData => "TlsOrProtocolError",
        _ => "ConnectionLost",
    })
}

/// A TCP connection to `host` at `port`, each address it resolves to tried in turn, every read and write limited to
/// `timeout`.
pub fn connect(host: &str, port: u16, timeout: Duration) -> Result<TcpStream, Lost> {
    let addresses = (host, port).to_socket_addrs().map_err(|_| Lost("NameNotResolved"))?;
    let mut last = Lost("NameNotResolved");
    for address in addresses {
        match TcpStream::connect_timeout(&address, timeout) {
            Ok(tcp) => {
                tcp.set_read_timeout(Some(timeout)).map_err(|e| lost(&e))?;
                tcp.set_write_timeout(Some(timeout)).map_err(|e| lost(&e))?;
                return Ok(tcp);
            }
            Err(e) => last = lost(&e),
        }
    }
    Err(last)
}

/// The name TLS checks a server's certificate against.
pub fn server_name(host: &str) -> Result<ServerName<'static>, Lost> {
    ServerName::try_from(host.to_owned()).map_err(|_| Lost("BadServerName"))
}

/// TLS on `tcp`, the handshake done. A certificate that didn't verify is `CertificateNotVerified`, apart from TLS
/// that failed otherwise.
pub fn handshake(config: Arc<ClientConfig>, name: ServerName<'static>, mut tcp: TcpStream) -> Result<Stream, Lost> {
    let mut connection = ClientConnection::new(config, name).map_err(|_| Lost("TlsError"))?;
    while connection.is_handshaking() {
        connection.complete_io(&mut tcp).map_err(|e| tls_lost(&e))?;
    }
    Ok(Stream::Tls(Box::new(StreamOwned::new(connection, tcp))))
}

fn tls_lost(e: &io::Error) -> Lost {
    match e.get_ref().and_then(|inner| inner.downcast_ref::<rustls::Error>()) {
        Some(rustls::Error::InvalidCertificate(_)) => Lost("CertificateNotVerified"),
        _ => Lost("TlsError"),
    }
}

/// The connection, before TLS starts and after.
#[derive(Debug)]
pub enum Stream {
    Plain(TcpStream),
    Tls(Box<StreamOwned<ClientConnection, TcpStream>>),
}

impl Stream {
    /// The DER bytes of the certificate the server presented: none before TLS.
    pub fn peer_certificate(&self) -> Option<Vec<u8>> {
        match self {
            Stream::Tls(tls) => tls.conn.peer_certificates().and_then(|c| c.first()).map(|c| c.as_ref().to_vec()),
            Stream::Plain(_) => None,
        }
    }

    /// Say goodbye to TLS, as far as the server still listens.
    pub fn close_notify(&mut self) {
        if let Stream::Tls(tls) = self {
            tls.conn.send_close_notify();
            let _ = tls.conn.complete_io(&mut tls.sock);
        }
    }

    /// End the connection both ways, at once.
    pub fn shutdown(&self) -> io::Result<()> {
        match self {
            Stream::Tls(tls) => tls.sock.shutdown(Shutdown::Both),
            Stream::Plain(tcp) => tcp.shutdown(Shutdown::Both),
        }
    }
}

impl Read for Stream {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        match self {
            Stream::Plain(s) => s.read(buf),
            Stream::Tls(s) => s.read(buf),
        }
    }
}

impl Write for Stream {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        match self {
            Stream::Plain(s) => s.write(buf),
            Stream::Tls(s) => s.write(buf),
        }
    }

    fn flush(&mut self) -> io::Result<()> {
        match self {
            Stream::Plain(s) => s.flush(),
            Stream::Tls(s) => s.flush(),
        }
    }
}
