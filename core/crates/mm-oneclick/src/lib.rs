//! RFC 8058's one-click unsubscribe (`curation.adapters.safe_http`): the one request MailMind makes to an address a
//! message named. Whoever wrote the message chose the URL, so it is vetted as hostile, guard for guard as the Python
//! app's `one_click_post`:
//!
//! - **https only**, to a host the URL names (`bad_scheme`), a login in the URL never sent;
//! - **every address the host resolves to is vetted** (`address::forbidden`) before anything connects: one that is
//!   private, loopback, link-local or otherwise internal refuses the whole host (`ssrf_blocked`);
//! - **the name is asked once**, and the connection goes to the address vetted, the certificate checked for the name
//!   on the roots the system trusts, so a DNS answer that changes can't send it elsewhere;
//! - **at most one redirect**, vetted again from the start;
//! - **each wait limited** (`TIMEOUT`), and so is the whole of each request (`WHOLE`), which Python's isn't; the
//!   response's head read up to a limit, its body not at all;
//! - **nothing of the response kept but its status**, and no cookie or credential sent.
//!
//! Where Python's `urllib` raises on a URL (brackets that hold no IPv6 address, a port that isn't one), this refuses it
//! (`bad_url`), and a path's `;params` are sent as written, which Python's `urlparse` drops.
//!
//! What it needs of the world is a `Network`: the system's resolver and sockets in real use. A test gives it one that
//! answers names from a table and dials its own server, and TLS that trusts a root of its own; the vetting is the same.
//! The host is asked of `Outside` before its name is looked up.

pub mod address;
pub mod url;

use std::fmt;
use std::io::{self, BufRead, BufReader, Read, Write};
use std::net::{IpAddr, SocketAddr, TcpStream, ToSocketAddrs};
use std::sync::Arc;
use std::time::{Duration, Instant};

use mm_kernel::seams::Outside;
use mm_net::Stream;
use rustls::ClientConfig;

/// Who is asking, in each request.
pub const USER_AGENT: &str = "MailMind-Unsub/1.0 (+list-unsubscribe one-click)";
/// RFC 8058's body, the one thing sent.
pub const BODY: &[u8] = b"List-Unsubscribe=One-Click";
/// The longest any one wait may be: the connection, each read and each write.
pub const TIMEOUT: Duration = Duration::from_secs(10);
/// The longest one request may take, its connection made to its response read.
pub const WHOLE: Duration = Duration::from_secs(30);
/// The longest line of a response's head, and the most header lines it may have (`http.client`'s limits).
pub const HEAD_LINE: usize = 65_536;
pub const HEADERS: usize = 100;
/// Where no port is named.
const HTTPS_PORT: u16 = 443;
/// The redirects one POST follows.
const REDIRECTS: u8 = 1;

/// How a POST went: whether the host took it, the status it answered (0 where none came back), and why not.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Posted {
    pub ok: bool,
    pub status: u16,
    pub reason: &'static str,
}

impl Posted {
    const fn failed(status: u16, reason: &'static str) -> Posted {
        Posted { ok: false, status, reason }
    }
}

/// What a POST reaches the network through.
pub trait Network: Send + Sync + fmt::Debug {
    /// The addresses `host` resolves to, each with `port`.
    fn resolve(&self, host: &str, port: u16) -> io::Result<Vec<SocketAddr>>;
    /// A TCP connection to `address`, waiting at most `timeout` for it.
    fn connect(&self, address: SocketAddr, timeout: Duration) -> io::Result<TcpStream>;
}

/// The system's resolver and its sockets.
#[derive(Debug)]
pub struct SystemNetwork;

impl Network for SystemNetwork {
    fn resolve(&self, host: &str, port: u16) -> io::Result<Vec<SocketAddr>> {
        Ok((host, port).to_socket_addrs()?.collect())
    }

    fn connect(&self, address: SocketAddr, timeout: Duration) -> io::Result<TcpStream> {
        TcpStream::connect_timeout(&address, timeout)
    }
}

/// The one-click POST.
#[derive(Debug)]
pub struct OneClick {
    network: Arc<dyn Network>,
    /// None for the roots the system trusts, read once, at the first POST.
    tls: Option<Arc<ClientConfig>>,
    outside: Arc<dyn Outside>,
    /// The longest any one wait may be, and the whole of one request.
    wait: Duration,
    whole: Duration,
}

/// What a POST is sent to: the host the URL names, its port, and its path and query.
#[derive(Debug)]
struct Target {
    host: String,
    port: u16,
    path: String,
}

/// The URL's target, or how it failed: not https or naming no host, or not a URL at all.
fn target(url: &str) -> Result<Target, Posted> {
    let parts = url::split(url).map_err(|_| Posted::failed(0, "bad_url"))?;
    let Some(host) = parts.hostname().filter(|_| parts.scheme.as_deref() == Some("https")) else {
        return Err(Posted::failed(0, "bad_scheme"));
    };
    let port = parts.port().map_err(|_| Posted::failed(0, "bad_url"))?;
    // Python's `p.port or 443`: a port of 0 is no port.
    let port = port.filter(|p| *p != 0).unwrap_or(HTTPS_PORT);
    let mut path = if parts.path.is_empty() { "/".to_owned() } else { parts.path.clone() };
    if let Some(query) = parts.query.as_deref().filter(|q| !q.is_empty()) {
        path.push('?');
        path.push_str(query);
    }
    Ok(Target { host, port, path })
}

/// The request's bytes, as `http.client` writes them; None where the path would break its request line, a control
/// character, a space or anything not ASCII in it.
fn request(target: &Target) -> Option<Vec<u8>> {
    if target.path.bytes().any(|b| b <= b' ' || b >= 0x7f) {
        return None;
    }
    let host = if target.host.contains(':') { format!("[{}]", target.host) } else { target.host.clone() };
    let mut out = format!(
        "POST {} HTTP/1.1\r\nAccept-Encoding: identity\r\nHost: {host}\r\n\
         Content-Type: application/x-www-form-urlencoded\r\nContent-Length: {}\r\nUser-Agent: {USER_AGENT}\r\n\
         Connection: close\r\n\r\n",
        target.path,
        BODY.len()
    )
    .into_bytes();
    out.extend_from_slice(BODY);
    Some(out)
}

/// A connection read until a deadline: each read waits at most `wait`, and none starts past `deadline`. A TLS peer
/// that leaves without saying goodbye has ended what it sent.
struct Timed {
    stream: Stream,
    wait: Duration,
    deadline: Instant,
}

impl Read for Timed {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        let left = self.deadline.saturating_duration_since(Instant::now());
        if left.is_zero() {
            return Err(io::ErrorKind::TimedOut.into());
        }
        self.stream.set_read_timeout(Some(left.min(self.wait)))?;
        match self.stream.read(buf) {
            Err(e) if e.kind() == io::ErrorKind::UnexpectedEof => Ok(0),
            read => read,
        }
    }
}

/// One line of a response's head, its line break included: empty at the end of the stream.
fn line(reader: &mut impl BufRead) -> io::Result<Vec<u8>> {
    let mut line = Vec::new();
    reader.by_ref().take(u64::try_from(HEAD_LINE).unwrap_or(u64::MAX) + 1).read_until(b'\n', &mut line)?;
    if line.len() > HEAD_LINE {
        return Err(io::ErrorKind::InvalidData.into());
    }
    Ok(line)
}

/// The header lines up to the blank one, or the end, joined where one continues another; more than `HEADERS` lines
/// is refused.
fn header_lines(reader: &mut impl BufRead) -> io::Result<Vec<String>> {
    let mut lines: Vec<String> = Vec::new();
    for _ in 0..=HEADERS {
        let raw = line(reader)?;
        if matches!(raw.as_slice(), b"\r\n" | b"\n" | b"") {
            return Ok(lines);
        }
        let text: String = raw.iter().map(|b| char::from(*b)).collect();
        let text = text.trim_end_matches(['\r', '\n']).to_owned();
        match lines.last_mut() {
            Some(last) if text.starts_with([' ', '\t']) => last.push_str(&text),
            _ => lines.push(text),
        }
    }
    Err(io::ErrorKind::InvalidData.into())
}

/// The status a status line names (`http.client`'s `_read_status`): HTTP/1.x or 0.9, a number from 100 to 999.
fn status_of(raw: &[u8]) -> io::Result<u16> {
    let text: String = raw.iter().map(|b| char::from(*b)).collect();
    let mut words = text.split_whitespace();
    let version = words.next().unwrap_or_default();
    if !(version == "HTTP/1.0" || version == "HTTP/0.9" || version.starts_with("HTTP/1.")) {
        return Err(io::ErrorKind::InvalidData.into());
    }
    let status = words.next().unwrap_or_default();
    match status.parse::<u16>() {
        Ok(status) if (100..=999).contains(&status) => Ok(status),
        _ => Err(io::ErrorKind::InvalidData.into()),
    }
}

/// A response's status and where it sends the request on: its `Location`, every one of them joined as Python joins
/// them, for a redirect.
fn response(reader: &mut impl BufRead) -> io::Result<(u16, Option<String>)> {
    let status = loop {
        let raw = line(reader)?;
        if raw.is_empty() {
            return Err(io::ErrorKind::UnexpectedEof.into());
        }
        let status = status_of(&raw)?;
        let headers = header_lines(reader)?;
        if status != 100 {
            break (status, headers);
        }
    };
    let (status, headers) = status;
    if !matches!(status, 301 | 302 | 303 | 307 | 308) {
        return Ok((status, None));
    }
    let locations: Vec<&str> = headers
        .iter()
        .filter_map(|h| h.split_once(':'))
        .filter(|(name, _)| name.trim().eq_ignore_ascii_case("location"))
        .map(|(_, value)| value.trim())
        .collect();
    Ok((status, Some(locations.join(", ")).filter(|l| !l.is_empty())))
}

impl OneClick {
    /// The POST as MailMind makes it: the system's resolver and sockets, and TLS verified on the roots the system
    /// trusts.
    pub fn system(outside: Arc<dyn Outside>) -> OneClick {
        OneClick { network: Arc::new(SystemNetwork), tls: None, outside, wait: TIMEOUT, whole: WHOLE }
    }

    /// The POST over another network and roots, as a test makes it: vetted the same.
    pub fn over(network: Arc<dyn Network>, tls: Arc<ClientConfig>, outside: Arc<dyn Outside>) -> OneClick {
        OneClick { network, tls: Some(tls), outside, wait: TIMEOUT, whole: WHOLE }
    }

    /// The same POST with other time limits, shorter ones for a test: each wait, and the whole of one request.
    #[must_use]
    pub fn waiting(self, wait: Duration, whole: Duration) -> OneClick {
        OneClick { wait, whole, ..self }
    }

    /// RFC 8058's POST to `url`: whether it went, the status the host answered, and why not. Nothing it does can
    /// reach an address `address::forbidden` names.
    pub fn post(&self, url: &str) -> Posted {
        self.hop(url, REDIRECTS)
    }

    fn hop(&self, url: &str, redirects: u8) -> Posted {
        let target = match target(url) {
            Ok(target) => target,
            Err(failed) => return failed,
        };
        if self.outside.reach(&target.host).is_err() {
            return Posted::failed(0, "dns");
        }
        let Ok(found) = self.network.resolve(&target.host, target.port) else { return Posted::failed(0, "dns") };
        // Any address the name has that is internal refuses the whole host, before anything connects.
        if found.iter().any(|a| address::forbidden(a.ip())) {
            return Posted::failed(0, "ssrf_blocked");
        }
        // The last, as Python's loop leaves it.
        let Some(vetted) = found.last().map(SocketAddr::ip) else { return Posted::failed(0, "dns") };
        let Some(request) = request(&target) else { return Posted::failed(0, "timeout") };
        let (status, location) = match self.exchange(vetted, &target, &request) {
            Ok(answered) => answered,
            Err(_) => return Posted::failed(0, "timeout"),
        };
        if (200..300).contains(&status) {
            return Posted { ok: true, status, reason: "ok" };
        }
        match location {
            // Vetted again, from the start.
            Some(location) if redirects > 0 => match url::join(url, &location) {
                Ok(next) => self.hop(&next, redirects - 1),
                Err(_) => Posted::failed(0, "bad_url"),
            },
            _ => Posted::failed(status, "bad_status"),
        }
    }

    /// The request sent to `vetted`, TLS checking the certificate for the target's host, and what came back.
    fn exchange(&self, vetted: IpAddr, target: &Target, request: &[u8]) -> io::Result<(u16, Option<String>)> {
        let failed = |_| io::Error::from(io::ErrorKind::ConnectionAborted);
        let deadline = Instant::now() + self.whole;
        let tcp = self.network.connect(SocketAddr::new(vetted, target.port), self.wait)?;
        tcp.set_read_timeout(Some(self.wait))?;
        tcp.set_write_timeout(Some(self.wait))?;
        let tls = match &self.tls {
            Some(tls) => tls.clone(),
            None => mm_net::tls::config(false).map_err(failed)?,
        };
        let name = mm_net::server_name(&target.host).map_err(failed)?;
        let mut stream = mm_net::handshake(tls, name, tcp).map_err(failed)?;
        if Instant::now() >= deadline {
            return Err(io::ErrorKind::TimedOut.into());
        }
        stream.write_all(request)?;
        stream.flush()?;
        response(&mut BufReader::new(Timed { stream, wait: self.wait, deadline }))
    }
}
