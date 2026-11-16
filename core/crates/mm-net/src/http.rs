//! One HTTP/1.1 exchange with an HTTP API (the AI providers'): where the API is (`Endpoint`), the connection to it, a
//! request with a JSON body or none, written whole, and the response read back, of `MOST` bytes at most. Each exchange
//! is a connection of its own, closed after it (`Connection: close`), and its time limit is a deadline for all of it.

use std::io::{Read, Write};
use std::net::{IpAddr, Shutdown, TcpStream};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, RecvTimeoutError};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use crate::{Lost, Stream, lost};

/// Where an API is: its scheme, host, port and the path its routes hang from.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Endpoint {
    pub tls: bool,
    pub host: String,
    pub port: u16,
    /// The path before the API's routes, without a trailing `/`.
    pub base: String,
}

impl Endpoint {
    /// An `http://` or `https://` URL (its scheme in any case) as an endpoint, or None for anything else: a URL with a blank or a control
    /// character inside it among those, as one would carry a line of its own into the request.
    pub fn parse(url: &str) -> Option<Endpoint> {
        let url = url.trim();
        if url.chars().any(|c| c.is_whitespace() || c.is_control()) {
            return None;
        }
        // The scheme in any case, as a URL's is.
        let (scheme, rest) = url.split_once("://")?;
        let tls = match scheme.to_ascii_lowercase().as_str() {
            "https" => true,
            "http" => false,
            _ => return None,
        };
        let (authority, path) = rest.split_at(rest.find('/').unwrap_or(rest.len()));
        if authority.is_empty() || authority.contains('@') {
            return None;
        }
        let default_port = if tls { 443 } else { 80 };
        let (host, port) = if let Some(bracketed) = authority.strip_prefix('[') {
            let (host, after) = bracketed.split_once(']')?;
            let port = match after.strip_prefix(':') {
                Some(port) => port.parse().ok()?,
                None if after.is_empty() => default_port,
                None => return None,
            };
            (host.to_owned(), port)
        } else {
            match authority.rsplit_once(':') {
                Some((host, port)) => (host.to_owned(), port.parse().ok()?),
                None => (authority.to_owned(), default_port),
            }
        };
        if host.is_empty() {
            return None;
        }
        let path = path.split(['?', '#']).next().unwrap_or_default();
        Some(Endpoint { tls, host, port, base: path.trim_end_matches('/').to_owned() })
    }

    /// Whether the host is this machine: `localhost`, or a loopback address.
    pub fn loopback(&self) -> bool {
        let host = self.host.to_ascii_lowercase();
        host == "localhost"
            || host.parse::<IpAddr>().is_ok_and(|ip| match ip {
                IpAddr::V4(v4) => v4.is_loopback(),
                IpAddr::V6(v6) => v6.is_loopback() || v6.to_ipv4_mapped().is_some_and(|v4| v4.is_loopback()),
            })
    }

    /// What the `Host` header says.
    pub fn host_header(&self) -> String {
        let host = if self.host.contains(':') { format!("[{}]", self.host) } else { self.host.clone() };
        if self.port == if self.tls { 443 } else { 80 } { host } else { format!("{host}:{}", self.port) }
    }

    /// `request` sent on a connection of its own and the response read back, all of it within `limit`: connecting,
    /// TLS's handshake, sending, and reading the whole answer. Past it the connection is ended under whatever waits on
    /// it, and the attempt is `Timeout`, however the peer trickles its answer in, even one whose end looked like the
    /// answer's. TLS is verified on the system's roots, on this machine as off it, with no pin standing in for the
    /// chain; or the connection is plain, as the URL says. Whether the host may be reached is asked of `Outside` by the
    /// caller, first.
    pub fn send(&self, request: &[u8], limit: Duration) -> Result<Response, Lost> {
        let deadline = Instant::now() + limit;
        let tcp = crate::connect_by(&self.host, self.port, deadline)?;
        let watch = Watch::start(&tcp, deadline)?;
        let mut stream = if self.tls {
            let config = crate::tls::config(false)?;
            match crate::handshake(config, crate::server_name(&self.host)?, tcp) {
                Ok(stream) => stream,
                Err(_) if watch.cut() => return Err(Lost("Timeout")),
                Err(lost) => return Err(lost),
            }
        } else {
            Stream::Plain(tcp)
        };
        let answered = exchange(&mut stream, request);
        // Ended under it, which can look like an answer that ended.
        let late = watch.cut();
        stream.close_notify();
        drop(watch);
        if late { Err(Lost("Timeout")) } else { answered }
    }
}

/// Ends a connection at its deadline, from a thread of its own, unless it is let go first: its socket shut down both
/// ways through a descriptor of the watch's own, which wakes a read or a write blocked on it and closes nothing (as
/// an `Interrupter` does).
struct Watch {
    stop: Option<mpsc::Sender<()>>,
    cut: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl Watch {
    fn start(tcp: &TcpStream, deadline: Instant) -> Result<Watch, Lost> {
        let socket = tcp.try_clone().map_err(|e| lost(&e))?;
        let (stop, stopped) = mpsc::channel::<()>();
        let cut = Arc::new(AtomicBool::new(false));
        let cutting = cut.clone();
        let thread = std::thread::Builder::new()
            .name("http-deadline".into())
            .spawn(move || {
                let left = deadline.saturating_duration_since(Instant::now());
                if let Err(RecvTimeoutError::Timeout) = stopped.recv_timeout(left) {
                    cutting.store(true, Ordering::SeqCst);
                    // A socket already shut down, or gone, has nothing left to wake.
                    let _ = socket.shutdown(Shutdown::Both);
                }
            })
            .map_err(|e| lost(&e))?;
        Ok(Watch { stop: Some(stop), cut, thread: Some(thread) })
    }

    /// Whether the deadline came and the connection was ended under it.
    fn cut(&self) -> bool {
        self.cut.load(Ordering::SeqCst)
    }
}

impl Drop for Watch {
    fn drop(&mut self) {
        // Let go, the watch's thread wakes at once.
        drop(self.stop.take());
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

/// How long to wait before sending a request again, as the providers' SDKs wait: what `retry-after-ms` or
/// `retry-after` says when it is up to a minute, else half a second, doubling with each try, up to eight.
pub fn backoff(response: Option<&Response>, retried: u32) -> Duration {
    let said = response.and_then(|r| {
        let ms = r.header("retry-after-ms").and_then(|v| v.trim().parse::<f64>().ok()).map(|ms| ms / 1000.0);
        ms.or_else(|| r.header("retry-after").and_then(|v| v.trim().parse::<f64>().ok()))
    });
    match said {
        Some(seconds) if (0.0..=60.0).contains(&seconds) => Duration::from_secs_f64(seconds),
        _ => Duration::from_secs_f64((0.5 * f64::from(1u32 << retried.min(4))).min(8.0)),
    }
}

/// The most a response may run to: a reply is a few kilobytes, and this leaves room for any the API sends.
const MOST: usize = 32 << 20;

/// What came back.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Response {
    pub status: u16,
    /// Each header, its name lower-cased.
    pub headers: Vec<(String, String)>,
    pub body: Vec<u8>,
}

impl Response {
    pub fn header(&self, name: &str) -> Option<&str> {
        self.headers.iter().find(|(n, _)| n == name).map(|(_, v)| v.as_str())
    }
}

/// The bytes of a request by `method`: the request line, `headers` (each a name and a value with no line break in
/// either), and the body, if it has one.
pub fn request(method: &str, path: &str, host: &str, headers: &[(&str, &str)], body: Option<&[u8]>) -> Vec<u8> {
    let mut out = format!("{method} {path} HTTP/1.1\r\nHost: {host}\r\n");
    for (name, value) in headers {
        out.push_str(name);
        out.push_str(": ");
        out.push_str(value);
        out.push_str("\r\n");
    }
    if let Some(body) = body {
        out.push_str(&format!("Content-Length: {}\r\n", body.len()));
    }
    out.push_str("Connection: close\r\n\r\n");
    let mut bytes = out.into_bytes();
    bytes.extend_from_slice(body.unwrap_or_default());
    bytes
}

/// `request` sent on `stream`, and the response to it.
pub fn exchange(stream: &mut Stream, request: &[u8]) -> Result<Response, Lost> {
    stream.write_all(request).map_err(|e| lost(&e))?;
    stream.flush().map_err(|e| lost(&e))?;
    read_response(stream)
}

fn find(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack.windows(needle.len()).position(|w| w == needle)
}

/// More of `stream` onto `buffer`: false at its end.
fn more(stream: &mut impl Read, buffer: &mut Vec<u8>) -> Result<bool, Lost> {
    let mut chunk = [0u8; 16 * 1024];
    let n = match stream.read(&mut chunk) {
        Ok(n) => n,
        // A TLS peer that closes without saying goodbye has still sent all it meant to.
        Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => 0,
        Err(e) => return Err(lost(&e)),
    };
    if buffer.len() + n > MOST {
        return Err(Lost("ResponseTooLarge"));
    }
    buffer.extend_from_slice(&chunk[..n]);
    Ok(n > 0)
}

/// A response off `stream`: its head, then a body of the length it declares, in chunks, or to the end.
pub fn read_response(stream: &mut impl Read) -> Result<Response, Lost> {
    let mut buffer = Vec::new();
    let end = loop {
        if let Some(at) = find(&buffer, b"\r\n\r\n") {
            break at;
        }
        if !more(stream, &mut buffer)? {
            return Err(Lost("ConnectionLost"));
        }
    };
    let head = String::from_utf8_lossy(&buffer[..end]).into_owned();
    let mut rest = buffer.split_off(end + 4);
    let mut lines = head.split("\r\n");
    let status_line = lines.next().unwrap_or_default();
    let status = status_line
        .split(' ')
        .nth(1)
        .and_then(|s| s.parse::<u16>().ok())
        .filter(|_| status_line.starts_with("HTTP/"))
        .ok_or(Lost("ProtocolError"))?;
    let headers: Vec<(String, String)> = lines
        .filter_map(|line| line.split_once(':'))
        .map(|(n, v)| (n.trim().to_ascii_lowercase(), v.trim().to_owned()))
        .collect();
    let named = |name: &str| headers.iter().find(|(n, _)| n == name).map(|(_, v)| v.clone());
    let body = if named("transfer-encoding").is_some_and(|t| t.to_ascii_lowercase().contains("chunked")) {
        loop {
            if let Some(body) = dechunk(&rest)? {
                break body;
            }
            if !more(stream, &mut rest)? {
                return Err(Lost("ConnectionLost"));
            }
        }
    } else if let Some(length) = named("content-length") {
        let length: usize = length.parse().map_err(|_| Lost("ProtocolError"))?;
        if length > MOST {
            return Err(Lost("ResponseTooLarge"));
        }
        while rest.len() < length {
            if !more(stream, &mut rest)? {
                return Err(Lost("ConnectionLost"));
            }
        }
        rest.truncate(length);
        rest
    } else {
        while more(stream, &mut rest)? {}
        rest
    };
    Ok(Response { status, headers, body })
}

/// A chunked body's bytes once `bytes` hold it through its last, empty chunk, None until then, or why it can't be
/// one: a size that isn't hex, or one past the most a response may run to.
fn dechunk(mut bytes: &[u8]) -> Result<Option<Vec<u8>>, Lost> {
    let mut out = Vec::new();
    loop {
        let Some(line_end) = find(bytes, b"\r\n") else { return Ok(None) };
        let size_text = String::from_utf8_lossy(&bytes[..line_end]);
        let size_text = size_text.split(';').next().unwrap_or_default().trim();
        let size = usize::from_str_radix(size_text, 16).map_err(|_| Lost("ProtocolError"))?;
        if size > MOST {
            return Err(Lost("ResponseTooLarge"));
        }
        bytes = &bytes[line_end + 2..];
        if size == 0 {
            return Ok(Some(out));
        }
        if bytes.len() < size + 2 {
            return Ok(None);
        }
        out.extend_from_slice(&bytes[..size]);
        bytes = &bytes[size + 2..];
    }
}
