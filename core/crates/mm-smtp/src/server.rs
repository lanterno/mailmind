//! A connection to an account's SMTP server: TLS, then a login, then the messages the outbox sends
//! (`SmtpTransport.connect` and `send`).

use std::time::Duration;

use mm_kernel::seams::{Outside, Secret};
use mm_net::{Lost, Stream};

use crate::client::Client;
use crate::error::Error;

/// Where a server listens and how TLS starts there.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Endpoint<'a> {
    pub host: &'a str,
    pub port: u16,
    /// STARTTLS on a plain connection (`smtp_starttls`, the default); else TLS from the first byte, as on 465.
    pub starttls: bool,
    /// The server is on this machine, so its certificate is pinned by the caller rather than verified.
    pub loopback: bool,
}

/// A server connected over TLS, greeted and EHLO'd, its certificate in hand, and not logged in yet.
#[derive(Debug)]
pub struct Server {
    client: Client<Stream>,
    certificate: Vec<u8>,
}

impl Server {
    /// Connect to `endpoint`, asking `outside` before the host is reached, and start TLS: STARTTLS after the greeting
    /// and EHLO, or from the first byte; then EHLO over it. Nothing that names the account is sent.
    pub fn connect(outside: &dyn Outside, endpoint: Endpoint<'_>, timeout: Duration) -> Result<Server, Error> {
        let config = mm_net::tls::config(endpoint.loopback).map_err(Error::Tls)?;
        outside.reach(endpoint.host).map_err(|_| Error::Tripped)?;
        let tcp = mm_net::connect(endpoint.host, endpoint.port, timeout).map_err(Error::Connect)?;
        let local = tcp.local_addr().map_err(|e| Error::Connect(mm_net::lost(&e)))?.ip();
        let name = mm_net::server_name(endpoint.host).map_err(Error::Tls)?;
        let mut client = if endpoint.starttls {
            let mut plain = Client::greet(tcp, local)?;
            plain.ehlo()?;
            let tcp = plain.starttls()?;
            Client::after_starttls(mm_net::handshake(config, name, tcp).map_err(Error::Tls)?, local)
        } else {
            Client::greet(mm_net::handshake(config, name, tcp).map_err(Error::Tls)?, local)?
        };
        client.ehlo()?;
        let certificate = client.stream().peer_certificate().ok_or(Error::Tls(Lost("NoCertificate")))?;
        Ok(Server { client, certificate })
    }

    /// The DER bytes of the certificate the server presented, for its pin to be checked before `login`.
    pub fn certificate(&self) -> &[u8] {
        &self.certificate
    }

    /// Log in (`smtplib.SMTP.login`). A refusal is `Error::Auth`, which counts toward a lockout when sent again.
    pub fn login(mut self, user: &str, password: &Secret) -> Result<Session, Error> {
        self.client.login(user, password)?;
        Ok(Session { client: self.client })
    }
}

/// A logged-in connection.
#[derive(Debug)]
pub struct Session {
    client: Client<Stream>,
}

impl Session {
    /// `smtplib.SMTP.sendmail(from, envelope, raw)`, as `SmtpTransport.send` watches it: `before_dot` is called once,
    /// just before the write that carries the message and its final dot, and a connection lost after that write with
    /// no answer is `Error::Unanswered`.
    pub fn send(
        &mut self,
        from: &str,
        envelope: &[String],
        raw: &[u8],
        before_dot: &mut dyn FnMut(),
    ) -> Result<(), Error> {
        self.client.send(from, envelope, raw, before_dot)
    }

    /// QUIT, its answer and any failure ignored, and the connection closed.
    pub fn quit(self) {
        let open = self.client.is_open();
        let mut stream = self.client.quit();
        if open {
            stream.close_notify();
        }
    }
}
