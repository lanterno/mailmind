//! SMTP on the wire, as `smtplib.SMTP.send` and `getreply` have it: a command goes in one write, and a reply is read
//! as its lines, each begun by its code, up to one whose code isn't followed by `-`.

use std::io::{self, Read, Write};

/// The longest reply line `smtplib` reads, its line end included (`_MAXLINE`).
const MAXLINE: usize = 8192;

/// A server's reply: its last line's code, -1 when a line began with no number (as `getreply` has it), and each line's
/// text after the code, Latin-1 read. It never leaves the crate: an error keeps only the code.
pub(crate) struct Reply {
    pub(crate) code: i32,
    pub(crate) lines: Vec<String>,
}

/// Why no reply came.
pub(crate) enum Broke {
    /// The connection closed or failed (`SMTPServerDisconnected`).
    Lost,
    /// A line longer than `MAXLINE` (`SMTPResponseException(500, "Line too long.")`).
    TooLong,
}

pub(crate) struct Wire<S> {
    pub(crate) stream: S,
    buffer: Vec<u8>,
}

impl<S: Read + Write> Wire<S> {
    pub(crate) fn new(stream: S) -> Wire<S> {
        Wire { stream, buffer: Vec::new() }
    }

    /// Whether bytes have arrived that no reply has read: there must be none when TLS starts.
    pub(crate) fn holds_unread(&self) -> bool {
        !self.buffer.is_empty()
    }

    /// `bytes` sent whole, as `sendall` sends them.
    pub(crate) fn send(&mut self, bytes: &[u8]) -> io::Result<()> {
        self.stream.write_all(bytes)?;
        self.stream.flush()
    }

    /// More bytes from the server: none means it closed the connection.
    fn fill(&mut self) -> io::Result<usize> {
        let mut chunk = [0u8; 8192];
        loop {
            match self.stream.read(&mut chunk) {
                Ok(n) => {
                    self.buffer.extend_from_slice(&chunk[..n]);
                    return Ok(n);
                }
                Err(e) if e.kind() == io::ErrorKind::Interrupted => {}
                Err(e) => return Err(e),
            }
        }
    }

    /// One line, its `\n` included, as `readline` reads it: what came before the server closed is a line too.
    fn line(&mut self) -> Result<Vec<u8>, Broke> {
        loop {
            if let Some(end) = self.buffer.iter().take(MAXLINE + 1).position(|&b| b == b'\n') {
                let line: Vec<u8> = self.buffer.drain(..=end).collect();
                return if line.len() > MAXLINE { Err(Broke::TooLong) } else { Ok(line) };
            }
            if self.buffer.len() > MAXLINE {
                return Err(Broke::TooLong);
            }
            match self.fill() {
                Ok(0) if self.buffer.is_empty() => return Err(Broke::Lost),
                Ok(0) => return Ok(std::mem::take(&mut self.buffer)),
                Ok(_) => {}
                Err(_) => return Err(Broke::Lost),
            }
        }
    }

    /// One reply, all its lines read.
    pub(crate) fn reply(&mut self) -> Result<Reply, Broke> {
        let mut lines = Vec::new();
        loop {
            let line = self.line()?;
            lines.push(latin1(trim(line.get(4..).unwrap_or_default())));
            let Some(code) = python_int(&line[..line.len().min(3)]) else {
                return Ok(Reply { code: -1, lines });
            };
            if line.get(3) != Some(&b'-') {
                return Ok(Reply { code, lines });
            }
        }
    }
}

/// `bytes` with the spaces, tabs and line ends around them gone, as `getreply` strips a line's text.
fn trim(bytes: &[u8]) -> &[u8] {
    let edge = |b: &u8| matches!(b, b' ' | b'\t' | b'\r' | b'\n');
    let start = bytes.iter().position(|b| !edge(b)).unwrap_or(bytes.len());
    let end = bytes.iter().rposition(|b| !edge(b)).map_or(start, |i| i + 1);
    &bytes[start..end]
}

fn latin1(bytes: &[u8]) -> String {
    bytes.iter().map(|&b| char::from(b)).collect()
}

/// Python's `int()` of a reply's first three bytes: ASCII whitespace around, a sign, and digits with single
/// underscores between them. Anything else is no number.
fn python_int(bytes: &[u8]) -> Option<i32> {
    let space = |b: &u8| matches!(b, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c);
    let start = bytes.iter().position(|b| !space(b))?;
    let end = bytes.iter().rposition(|b| !space(b))? + 1;
    let mut digits = &bytes[start..end];
    let negative = digits.first() == Some(&b'-');
    if matches!(digits.first(), Some(b'+' | b'-')) {
        digits = &digits[1..];
    }
    if !digits.first().is_some_and(u8::is_ascii_digit) || !digits.last().is_some_and(u8::is_ascii_digit) {
        return None;
    }
    let mut value = 0i32;
    let mut after_underscore = false;
    for &b in digits {
        match b {
            b'0'..=b'9' => {
                value = value * 10 + i32::from(b - b'0');
                after_underscore = false;
            }
            b'_' if !after_underscore => after_underscore = true,
            _ => return None,
        }
    }
    Some(if negative { -value } else { value })
}
