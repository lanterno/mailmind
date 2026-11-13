//! A conversation with an SMTP server over any stream, as `smtplib.SMTP` holds one: the greeting, EHLO, STARTTLS,
//! AUTH, a message's transaction and QUIT. `Server` and `Session` hold one over TLS; a test can hold one over a script.

use std::collections::BTreeMap;
use std::fmt;
use std::io::{Read, Write};
use std::net::IpAddr;

use mm_kernel::seams::Secret;
use mm_net::Lost;

use crate::error::Error;
use crate::wire::{Broke, Reply, Wire};

/// How many challenges AUTH answers before it gives up on the server (`_MAXCHALLENGE`).
const MAXCHALLENGE: u32 = 5;

/// The AUTH mechanisms spoken here, in `smtplib.SMTP.login`'s order of preference. CRAM-MD5, which `smtplib` tries
/// first, isn't one: it would need an MD5 the crate doesn't have.
#[derive(Clone, Copy)]
enum Mechanism {
    Plain,
    Login,
}

impl Mechanism {
    fn name(self) -> &'static str {
        match self {
            Mechanism::Plain => "PLAIN",
            Mechanism::Login => "LOGIN",
        }
    }
}

/// An SMTP conversation over `stream`, at the point `smtplib.SMTP` would be.
pub struct Client<S> {
    wire: Wire<S>,
    /// What EHLO names this end by: the connection's own address, as an address literal.
    name: String,
    /// The extensions the last EHLO listed, by their lowercased names, with their parameters (`esmtp_features`).
    features: BTreeMap<String, String>,
    /// Whether the connection is still held: `smtplib.SMTP.close` lets it go after a failure, and QUIT isn't sent.
    open: bool,
}

impl<S> fmt::Debug for Client<S> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Client").field("open", &self.open).finish_non_exhaustive()
    }
}

impl<S: Read + Write> Client<S> {
    /// The server's greeting read from `stream`: `smtplib.SMTP.connect`, and the `SMTPConnectError` its constructor
    /// raises for a code other than 220. `local` is the connection's own address, which EHLO names.
    pub fn greet(stream: S, local: IpAddr) -> Result<Client<S>, Error> {
        let mut client = Client::after_starttls(stream, local);
        let reply = client.reply()?;
        if reply.code != 220 {
            client.open = false;
            return Err(Error::Greeting(reply.code));
        }
        Ok(client)
    }

    /// The conversation going on over `stream` once TLS has started on it, nothing the server said before kept, as
    /// `smtplib.SMTP.starttls` leaves it.
    pub fn after_starttls(stream: S, local: IpAddr) -> Client<S> {
        Client { wire: Wire::new(stream), name: address_literal(local), features: BTreeMap::new(), open: true }
    }

    /// The stream the conversation is held over.
    pub fn stream(&self) -> &S {
        &self.wire.stream
    }

    pub(crate) fn is_open(&self) -> bool {
        self.open
    }

    /// `smtplib.SMTP.ehlo`: the extensions the server lists, none when it answers other than 250.
    pub fn ehlo(&mut self) -> Result<(), Error> {
        self.features.clear();
        let reply = self.command(&format!("ehlo {}", self.name))?;
        if reply.code == -1 && reply.lines.len() == 1 && reply.lines[0].is_empty() {
            self.open = false;
            return Err(Error::Disconnected);
        }
        if reply.code == 250 {
            for line in reply.lines.iter().skip(1) {
                self.learn(line);
            }
        }
        Ok(())
    }

    /// One line of EHLO's answer, as `smtplib.SMTP.ehlo` reads it: an old-style `AUTH=`, or a name and its parameters.
    fn learn(&mut self, line: &str) {
        if let Some(head) = line.get(..5)
            && head.eq_ignore_ascii_case("auth=")
        {
            let auth = self.features.entry("auth".to_owned()).or_default();
            auth.push(' ');
            auth.push_str(&line[5..]);
            return;
        }
        let end = line
            .char_indices()
            .find(|&(i, c)| !(c.is_ascii_alphanumeric() || (i > 0 && c == '-')))
            .map_or(line.len(), |(i, _)| i);
        if end == 0 {
            return;
        }
        let feature = line[..end].to_ascii_lowercase();
        let params = line[end..].trim_matches(python_space);
        if feature == "auth" {
            let auth = self.features.entry(feature).or_default();
            auth.push(' ');
            auth.push_str(params);
        } else {
            self.features.insert(feature, params.to_owned());
        }
    }

    fn feature(&self, name: &str) -> Option<&str> {
        self.features.get(name).map(String::as_str)
    }

    /// `smtplib.SMTP.starttls` up to its handshake: STARTTLS asked for and agreed to, and the stream handed back for
    /// TLS to start on. A server that doesn't offer it is left, never spoken to in plain text past EHLO; one that
    /// sent anything after its 220 is left too, since what it sent would read as if it came over TLS.
    pub fn starttls(mut self) -> Result<S, Error> {
        if self.feature("starttls").is_none() {
            return Err(Error::Failed);
        }
        let reply = self.command("STARTTLS")?;
        if reply.code != 220 {
            return Err(Error::Response(reply.code));
        }
        if self.wire.holds_unread() {
            return Err(Error::Tls(Lost("SpokeBeforeTls")));
        }
        Ok(self.wire.stream)
    }

    /// `smtplib.SMTP.login`: PLAIN, then LOGIN, as the server offers them, until one succeeds. The password goes only
    /// in AUTH's lines, and each buffer that held it is wiped once it is sent.
    pub fn login(&mut self, user: &str, password: &Secret) -> Result<(), Error> {
        let Some(offered) = self.feature("auth") else {
            return Err(Error::Failed);
        };
        let offered: Vec<&str> = offered.split(python_space).filter(|m| !m.is_empty()).collect();
        let mechanisms: Vec<Mechanism> =
            [Mechanism::Plain, Mechanism::Login].into_iter().filter(|m| offered.contains(&m.name())).collect();
        if mechanisms.is_empty() {
            return Err(Error::Failed);
        }
        if !user.is_ascii() || !password.expose().is_ascii() {
            return Err(Error::Unsendable);
        }
        let mut last = Error::Failed;
        for mechanism in mechanisms {
            match self.auth(mechanism, user, password) {
                Err(Error::Auth(code)) => last = Error::Auth(code),
                done => return done,
            }
        }
        Err(last)
    }

    /// `smtplib.SMTP.auth` with an initial response: PLAIN's `\0user\0password`, or LOGIN's user; then each
    /// challenge answered, PLAIN's with the same and LOGIN's with the password (`auth_login` counts the user as
    /// the first answer). 235, or 503 (already logged in), is a login.
    fn auth(&mut self, mechanism: Mechanism, user: &str, password: &Secret) -> Result<(), Error> {
        let first = match mechanism {
            Mechanism::Plain => encoded_line("AUTH PLAIN ", plain(user, password).expose().as_bytes()),
            Mechanism::Login => encoded_line("AUTH LOGIN ", user.as_bytes()),
        };
        let mut reply = self.exchange(first.expose().as_bytes())?;
        drop(first);
        let mut challenges = 1;
        while reply.code == 334 {
            challenges += 1;
            let answer = match mechanism {
                Mechanism::Plain => encoded_line("", plain(user, password).expose().as_bytes()),
                Mechanism::Login => encoded_line("", password.expose().as_bytes()),
            };
            reply = self.exchange(answer.expose().as_bytes())?;
            if challenges > MAXCHALLENGE {
                return Err(Error::Failed);
            }
        }
        match reply.code {
            235 | 503 => Ok(()),
            code => Err(Error::Auth(code)),
        }
    }

    /// `smtplib.SMTP.sendmail(from, envelope, raw)`: MAIL FROM, with the message's size when the server takes SIZE; a
    /// RCPT TO for each recipient, where every one refused is a refusal and some refused is still a send; DATA; and
    /// the message dot-stuffed, its final dot in the same write. `before_dot` is called once, just before that write.
    pub fn send(
        &mut self,
        from: &str,
        envelope: &[String],
        raw: &[u8],
        before_dot: &mut dyn FnMut(),
    ) -> Result<(), Error> {
        if envelope.is_empty() {
            return Err(Error::NoRecipients);
        }
        let size = if self.feature("size").is_some() { format!(" size={}", raw.len()) } else { String::new() };
        let code = self.command(&format!("mail from:{}{size}", quote_address(from)))?.code;
        if code != 250 {
            self.abandon(code);
            return Err(Error::Sender(code));
        }
        let mut refused: Vec<(&str, i32)> = Vec::new();
        for to in envelope {
            let code = self.command(&format!("rcpt to:{}", quote_address(to)))?.code;
            if code != 250 && code != 251 {
                match refused.iter_mut().find(|(address, _)| *address == to.as_str()) {
                    Some(seen) => seen.1 = code,
                    None => refused.push((to.as_str(), code)),
                }
            }
            if code == 421 {
                self.open = false;
                return Err(Error::Recipients(refused.iter().map(|&(_, code)| code).collect()));
            }
        }
        if refused.len() == envelope.len() {
            self.rset();
            return Err(Error::Recipients(refused.iter().map(|&(_, code)| code).collect()));
        }
        let code = self.command("data")?.code;
        if code != 354 {
            return Err(Error::Data(code));
        }
        let message = stuffed(raw);
        before_dot();
        self.write(&message)?;
        let code = match self.wire.reply() {
            Ok(reply) => reply.code,
            Err(broke) => {
                self.open = false;
                return Err(match broke {
                    Broke::Lost => Error::Unanswered,
                    Broke::TooLong => Error::Response(500),
                });
            }
        };
        if code != 250 {
            self.abandon(code);
            return Err(Error::Data(code));
        }
        Ok(())
    }

    /// `smtplib.SMTP.quit`: QUIT, its answer and any failure ignored, unless the connection was already let go. The
    /// stream is handed back to be closed.
    pub fn quit(mut self) -> S {
        if self.open {
            let _ = self.command("quit");
        }
        self.wire.stream
    }

    /// After a refusal in a transaction: 421 lets the connection go, and anything else resets it (`sendmail`'s
    /// `close` or `_rset`).
    fn abandon(&mut self, code: i32) {
        if code == 421 {
            self.open = false;
        } else {
            self.rset();
        }
    }

    /// `smtplib.SMTP._rset`: RSET, its failure ignored.
    fn rset(&mut self) {
        let _ = self.command("rset");
    }

    /// `smtplib.SMTP.docmd`: `line` sent and its reply read. A line holding a line break, or a character outside
    /// ASCII, is never sent (`putcmd`'s `ValueError`, and the ASCII it encodes in).
    fn command(&mut self, line: &str) -> Result<Reply, Error> {
        if line.contains(['\r', '\n']) || !line.is_ascii() {
            return Err(Error::Unsendable);
        }
        let mut bytes = Vec::with_capacity(line.len() + 2);
        bytes.extend_from_slice(line.as_bytes());
        bytes.extend_from_slice(b"\r\n");
        self.exchange(&bytes)
    }

    fn exchange(&mut self, bytes: &[u8]) -> Result<Reply, Error> {
        self.write(bytes)?;
        self.reply()
    }

    fn write(&mut self, bytes: &[u8]) -> Result<(), Error> {
        if !self.open {
            return Err(Error::Disconnected);
        }
        self.wire.send(bytes).map_err(|_| {
            self.open = false;
            Error::Disconnected
        })
    }

    fn reply(&mut self) -> Result<Reply, Error> {
        self.wire.reply().map_err(|broke| {
            self.open = false;
            match broke {
                Broke::Lost => Error::Disconnected,
                Broke::TooLong => Error::Response(500),
            }
        })
    }
}

/// An address literal for `ip`, as EHLO names this end: `[127.0.0.1]`, or `[IPv6:::1]`.
fn address_literal(ip: IpAddr) -> String {
    match ip.to_canonical() {
        IpAddr::V4(v4) => format!("[{v4}]"),
        IpAddr::V6(v6) => format!("[IPv6:{v6}]"),
    }
}

/// `smtplib.quoteaddr` of an addr-spec, as the builder's envelope and the account's address are: in angle brackets,
/// unless it already is.
fn quote_address(address: &str) -> String {
    let address = address.trim_ascii();
    if address.starts_with('<') && address.ends_with('>') { address.to_owned() } else { format!("<{address}>") }
}

/// The message as DATA sends it (`smtplib.SMTP.data`): a `.` that begins a line doubled, a line end added unless
/// it ends in one, and the final dot.
fn stuffed(raw: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(raw.len() + raw.len() / 64 + 5);
    let mut line_start = true;
    for &b in raw {
        if line_start && b == b'.' {
            out.push(b'.');
        }
        out.push(b);
        line_start = b == b'\n';
    }
    if !out.ends_with(b"\r\n") {
        out.extend_from_slice(b"\r\n");
    }
    out.extend_from_slice(b".\r\n");
    out
}

/// PLAIN's credentials, `\0user\0password`, in a buffer wiped when it is dropped.
fn plain(user: &str, password: &Secret) -> Secret {
    let mut payload = String::with_capacity(2 + user.len() + password.expose().len());
    payload.push('\0');
    payload.push_str(user);
    payload.push('\0');
    payload.push_str(password.expose());
    Secret::new(payload)
}

/// `prefix`, then `payload` in base64 with no line breaks (`encode_base64(.., eol='')`), then CRLF: one of AUTH's
/// lines, in a buffer sized for it at once, so no copy is left behind as it grows, and wiped when it is dropped.
fn encoded_line(prefix: &str, payload: &[u8]) -> Secret {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut line = String::with_capacity(prefix.len() + payload.len().div_ceil(3) * 4 + 2);
    line.push_str(prefix);
    for chunk in payload.chunks(3) {
        let at = |i: usize| u32::from(chunk.get(i).copied().unwrap_or(0));
        let n = (at(0) << 16) | (at(1) << 8) | at(2);
        let sextet = |shift: u32| char::from(ALPHABET[((n >> shift) & 63) as usize]);
        line.push(sextet(18));
        line.push(sextet(12));
        line.push(if chunk.len() > 1 { sextet(6) } else { '=' });
        line.push(if chunk.len() > 2 { sextet(0) } else { '=' });
    }
    line.push_str("\r\n");
    Secret::new(line)
}

/// Whether `c` is whitespace to Python's `str.split` and `str.strip`, among the Latin-1 characters a reply is read as.
fn python_space(c: char) -> bool {
    matches!(c, '\t'..='\r' | '\x1c'..='\x1f' | ' ' | '\u{85}' | '\u{a0}')
}
