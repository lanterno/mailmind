//! A connection to an account's IMAP server: TLS, then a login, then the commands the Mailbox sends.

use std::io;
use std::net::{TcpStream, ToSocketAddrs};
use std::time::Duration;

use mm_kernel::errors::Fault;
use mm_kernel::seams::{Outside, Secret};
use mm_mailbox::ports::{MailSession, SessionError};
use mm_mailbox::roles::Folder;
use mm_mailbox::session::ServerError;
use rustls::pki_types::ServerName;
use rustls::{ClientConnection, StreamOwned};

use crate::codec::{self, Said};
use crate::list::{self, head_and_literal, strip_prefix_ignoring_case};
use crate::tls;
use crate::wire::{Piece, Stream, Wire};

/// Where a server listens and how TLS starts there.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Endpoint<'a> {
    pub host: &'a str,
    pub port: u16,
    /// STARTTLS on a plain connection, as Proton Bridge has it; else TLS from the first byte.
    pub starttls: bool,
    /// The server is on this machine, so its certificate is pinned by the caller rather than verified.
    pub loopback: bool,
}

/// Why a connection or a command didn't go, by its class alone: never what a server said.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Error {
    /// The server couldn't be reached, the connection was lost, TLS failed or the login was refused: the Python app's
    /// `UNREACHABLE`, where asking again later may help.
    Unreachable(&'static str),
    /// The server refused a command, or a value couldn't be sent: asking again won't help.
    Refused(&'static str),
}

impl From<Error> for ServerError {
    fn from(error: Error) -> ServerError {
        match error {
            Error::Unreachable(class) => ServerError::Unreachable(class),
            Error::Refused(class) => ServerError::Refused(class),
        }
    }
}

impl From<Error> for SessionError {
    fn from(error: Error) -> SessionError {
        match error {
            Error::Unreachable(class) => SessionError::Unreachable(class),
            Error::Refused(class) => SessionError::Failed(Fault::new(class)),
        }
    }
}

fn lost(e: &io::Error) -> Error {
    match e.kind() {
        io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock => Error::Unreachable("Timeout"),
        io::ErrorKind::ConnectionRefused => Error::Unreachable("ConnectionRefused"),
        io::ErrorKind::InvalidData => Error::Unreachable("TlsOrProtocolError"),
        _ => Error::Unreachable("ConnectionLost"),
    }
}

/// A command's untagged responses, and the status of its tagged one (`OK`, `NO` or `BAD`) with the rest of its line.
pub(crate) struct Answer {
    pub(crate) untagged: Vec<Vec<Piece>>,
    pub(crate) status: String,
    text: String,
    /// The tagged line, as it came.
    pub(crate) tagged: String,
}

impl Answer {
    /// What each untagged response said, and then what the tagged one's code said, as imap-proto reads them.
    pub(crate) fn said(&self) -> Vec<Said> {
        let mut out: Vec<Said> = self.untagged.iter().map(|r| codec::read(&codec::raw(r))).collect();
        out.push(codec::read(format!("{}\r\n", self.tagged).as_bytes()));
        out
    }

    /// The server did it, or a refusal of `class`.
    pub(crate) fn done(self, class: &'static str) -> Result<Answer, Error> {
        if self.status == "OK" { Ok(self) } else { Err(Error::Refused(class)) }
    }
}

#[derive(Debug)]
pub(crate) struct Connection {
    pub(crate) wire: Wire,
    sent: u32,
    pub(crate) capabilities: Vec<String>,
}

impl Connection {
    pub(crate) fn command(&mut self, command: &[u8]) -> Result<Answer, Error> {
        self.exchange(command, None)
    }

    /// Send `command`, with `literal` after it as a literal once the server asks for it (`+`); then read to the tagged
    /// answer. A server that refuses the literal answers before asking, and it is never sent.
    pub(crate) fn exchange(&mut self, command: &[u8], literal: Option<&[u8]>) -> Result<Answer, Error> {
        self.sent += 1;
        let tag = format!("MM{}", self.sent);
        let mut line = Vec::with_capacity(tag.len() + 1 + command.len() + 16);
        line.extend_from_slice(tag.as_bytes());
        line.push(b' ');
        line.extend_from_slice(command);
        if let Some(literal) = literal {
            line.extend_from_slice(format!(" {{{}}}", literal.len()).as_bytes());
        }
        let sent = self.wire.send(&line);
        line.fill(0);
        sent.map_err(|e| lost(&e))?;
        let mut untagged = Vec::new();
        let mut waiting = literal;
        loop {
            let response = self.wire.response().map_err(|e| lost(&e))?;
            let Some(Piece::Text(first)) = response.first() else {
                return Err(Error::Unreachable("ProtocolError"));
            };
            if let Some(rest) = first.strip_prefix(&tag).and_then(|r| r.strip_prefix(' ')) {
                let (status, text) = rest.split_once(' ').unwrap_or((rest, ""));
                let (status, text) = (status.to_ascii_uppercase(), text.to_owned());
                return Ok(Answer { untagged, status, text, tagged: first.clone() });
            }
            if first.starts_with('+') {
                match waiting.take() {
                    Some(bytes) => self.wire.send(bytes).map_err(|e| lost(&e))?,
                    None => return Err(Error::Unreachable("ProtocolError")),
                }
                continue;
            }
            if strip_prefix_ignoring_case(first, "* BYE").is_some() {
                return Err(Error::Unreachable("Abort"));
            }
            untagged.push(response);
        }
    }

    fn learn_capabilities(&mut self, said: &str) -> Result<(), Error> {
        if let Some(found) = capability_code(said) {
            self.capabilities = found;
            return Ok(());
        }
        let answer = self.command(b"CAPABILITY")?;
        for response in &answer.untagged {
            if let Some(Piece::Text(first)) = response.first()
                && let Some(rest) = strip_prefix_ignoring_case(first, "* CAPABILITY ")
            {
                self.capabilities = rest.split_whitespace().map(str::to_ascii_uppercase).collect();
            }
        }
        Ok(())
    }
}

/// `[CAPABILITY ...]` in a server's line, as the capabilities it lists.
fn capability_code(said: &str) -> Option<Vec<String>> {
    let upper = said.to_ascii_uppercase();
    let start = upper.find("[CAPABILITY ")? + "[CAPABILITY ".len();
    let end = upper[start..].find(']')? + start;
    Some(upper[start..end].split_whitespace().map(str::to_owned).collect())
}

/// A server connected over TLS, its certificate in hand, and not logged in yet.
#[derive(Debug)]
pub struct Server {
    pub(crate) connection: Connection,
    certificate: Vec<u8>,
}

impl Server {
    /// Connect to `endpoint` through `outside` and start TLS. Nothing that names the account is sent.
    pub fn connect(outside: &dyn Outside, endpoint: Endpoint<'_>, timeout: Duration) -> Result<Server, Error> {
        if !endpoint.loopback {
            return Err(Error::Unreachable("VerifiedTlsNotOnTheRustServerYet"));
        }
        outside.reach(endpoint.host).map_err(|_| Error::Unreachable("Tripped"))?;
        let tcp = connect_tcp(endpoint.host, endpoint.port, timeout)?;
        let config = tls::loopback().map_err(|_| Error::Unreachable("TlsError"))?;
        let name = ServerName::try_from(endpoint.host.to_owned()).map_err(|_| Error::Unreachable("BadServerName"))?;
        let starttls = endpoint.starttls;
        let stream = if starttls {
            Stream::Plain(tcp)
        } else {
            Stream::Tls(Box::new(handshake(config.clone(), name.clone(), tcp)?))
        };
        let mut connection = Connection { wire: Wire::new(stream), sent: 0, capabilities: Vec::new() };
        let greeting = connection.wire.response().map_err(|e| lost(&e))?;
        let Some(Piece::Text(greeting)) = greeting.first() else {
            return Err(Error::Unreachable("ProtocolError"));
        };
        if strip_prefix_ignoring_case(greeting, "* OK").is_none()
            && strip_prefix_ignoring_case(greeting, "* PREAUTH").is_none()
        {
            return Err(Error::Unreachable("Abort"));
        }
        let greeting = greeting.clone();
        connection.learn_capabilities(&greeting)?;
        if starttls {
            if !connection.capabilities.iter().any(|c| c == "STARTTLS") {
                return Err(Error::Unreachable("Abort"));
            }
            let answer = connection.command(b"STARTTLS")?;
            if answer.status != "OK" || connection.wire.holds_unread() {
                return Err(Error::Unreachable("TlsError"));
            }
            let Connection { wire, sent, capabilities } = connection;
            let Stream::Plain(tcp) = wire.stream else {
                return Err(Error::Unreachable("TlsError"));
            };
            let tls = handshake(config, name, tcp)?;
            connection = Connection { wire: Wire::new(Stream::Tls(Box::new(tls))), sent, capabilities };
            connection.learn_capabilities("")?;
        }
        let certificate = match &connection.wire.stream {
            Stream::Tls(tls) => tls.conn.peer_certificates().and_then(|c| c.first()).map(|c| c.as_ref().to_vec()),
            Stream::Plain(_) => None,
        }
        .ok_or(Error::Unreachable("NoCertificate"))?;
        Ok(Server { connection, certificate })
    }

    /// The DER bytes of the certificate the server presented, for its pin to be checked before `login`.
    pub fn certificate(&self) -> &[u8] {
        &self.certificate
    }

    /// Log in. A login the server refused is unreachable, as the Python app's `LoginUnreachable` is; a user or a
    /// password holding a line break or NUL is never sent, since it would end LOGIN's line and start another.
    pub fn login(mut self, user: &str, password: &Secret) -> Result<Session, Error> {
        let unsendable = |s: &str| s.contains(['\r', '\n', '\0']);
        if unsendable(user) || unsendable(password.expose()) {
            return Err(Error::Refused("Unsendable"));
        }
        let mut command = Vec::with_capacity(16 + user.len() + password.expose().len());
        command.extend_from_slice(b"LOGIN ");
        command.extend_from_slice(quote(user).as_bytes());
        command.push(b' ');
        let mut quoted = quote(password.expose()).into_bytes();
        command.extend_from_slice(&quoted);
        quoted.fill(0);
        let answer = self.connection.command(&command);
        command.fill(0);
        let answer = answer?;
        if answer.status != "OK" {
            return Err(Error::Unreachable("LoginRefused"));
        }
        self.connection.learn_capabilities(&answer.text)?;
        Ok(Session { connection: self.connection })
    }
}

/// An IMAP quoted string.
fn quote(s: &str) -> String {
    format!("\"{}\"", s.replace('\\', "\\\\").replace('"', "\\\""))
}

fn connect_tcp(host: &str, port: u16, timeout: Duration) -> Result<TcpStream, Error> {
    let addresses = (host, port).to_socket_addrs().map_err(|_| Error::Unreachable("NameNotResolved"))?;
    let mut last = Error::Unreachable("NameNotResolved");
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

fn handshake(
    config: std::sync::Arc<rustls::ClientConfig>,
    name: ServerName<'static>,
    mut tcp: TcpStream,
) -> Result<StreamOwned<ClientConnection, TcpStream>, Error> {
    let mut connection = ClientConnection::new(config, name).map_err(|_| Error::Unreachable("TlsError"))?;
    while connection.is_handshaking() {
        connection.complete_io(&mut tcp).map_err(|_| Error::Unreachable("TlsError"))?;
    }
    Ok(StreamOwned::new(connection, tcp))
}

/// A logged-in connection.
#[derive(Debug)]
pub struct Session {
    pub(crate) connection: Connection,
}

impl Session {
    /// The server's capabilities, as it listed them once logged in.
    pub fn capabilities(&self) -> &[String] {
        &self.connection.capabilities
    }

    fn list(&mut self) -> Result<Vec<Folder>, Error> {
        let answer = self.connection.command(b"LIST \"\" \"*\"")?;
        if answer.status != "OK" {
            return Err(Error::Refused("ListRefused"));
        }
        let lines: Vec<_> = answer.untagged.into_iter().filter_map(|r| head_and_literal(r, "* LIST ")).collect();
        Ok(list::parse_list(&lines))
    }

    fn status_of(&mut self, folder: &str) -> Result<(i64, i64), Error> {
        let argument = list::mailbox_arg(folder).ok_or(Error::Refused("BadMailbox"))?;
        let answer = self.connection.command(format!("STATUS {argument} (UNSEEN MESSAGES)").as_bytes())?;
        if answer.status != "OK" {
            return Err(Error::Refused("StatusRefused"));
        }
        let (mut unseen, mut total) = (0, 0);
        for response in &answer.untagged {
            let text: Vec<&str> =
                response.iter().filter_map(|p| if let Piece::Text(t) = p { Some(t.as_str()) } else { None }).collect();
            let Some(first) = text.first() else { continue };
            if strip_prefix_ignoring_case(first, "* STATUS ").is_none() {
                continue;
            }
            let joined = text.join(" ");
            let Some(open) = joined.rfind('(') else { continue };
            let items: Vec<&str> = joined[open + 1..].trim_end_matches(')').split_whitespace().collect();
            for pair in items.chunks(2) {
                if let [name, value] = pair
                    && let Ok(value) = value.parse::<i64>()
                {
                    match name.to_ascii_uppercase().as_str() {
                        "UNSEEN" => unseen = value,
                        "MESSAGES" => total = value,
                        _ => {}
                    }
                }
            }
        }
        Ok((unseen, total))
    }
}

impl MailSession for Session {
    fn folders(&mut self) -> Result<Vec<Folder>, SessionError> {
        Ok(self.list()?)
    }

    fn status(&mut self, folder: &str) -> Result<(i64, i64), SessionError> {
        Ok(self.status_of(folder)?)
    }

    fn logout(&mut self) {
        let _ = self.connection.command(b"LOGOUT");
        if let Stream::Tls(tls) = &mut self.connection.wire.stream {
            tls.conn.send_close_notify();
            let _ = tls.conn.complete_io(&mut tls.sock);
        }
    }
}
