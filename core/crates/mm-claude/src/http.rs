//! One HTTP/1.1 exchange over a connection mm-net opened: a request, with a JSON body or none, written whole, and the
//! response read back. Each exchange is a connection of its own, closed after it (`Connection: close`).

use std::io::{Read, Write};

use mm_net::{Lost, Stream, lost};

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
