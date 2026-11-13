//! IMAP on the wire: a command a line, and a response a line, with any literal (`{n}` and then n bytes) read into it.

use std::io::{self, Read, Write};

pub(crate) use mm_net::Stream;

/// A piece of a response: its text, or a literal's bytes.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum Piece {
    Text(String),
    Literal(Vec<u8>),
}

/// The longest line or literal a response may hold before the connection is given up on.
const MOST: usize = 16 << 20;

#[derive(Debug)]
pub(crate) struct Wire {
    pub(crate) stream: Stream,
    buffer: Vec<u8>,
}

impl Wire {
    pub(crate) fn new(stream: Stream) -> Wire {
        Wire { stream, buffer: Vec::new() }
    }

    /// Whether bytes have arrived that nothing has read yet: there must be none when TLS starts.
    pub(crate) fn holds_unread(&self) -> bool {
        !self.buffer.is_empty()
    }

    fn fill(&mut self) -> io::Result<()> {
        let mut chunk = [0u8; 8192];
        let n = self.stream.read(&mut chunk)?;
        if n == 0 {
            return Err(io::Error::new(io::ErrorKind::UnexpectedEof, "the server closed the connection"));
        }
        self.buffer.extend_from_slice(&chunk[..n]);
        Ok(())
    }

    fn line(&mut self) -> io::Result<Vec<u8>> {
        loop {
            if let Some(end) = self.buffer.windows(2).position(|w| w == b"\r\n") {
                let line = self.buffer[..end].to_vec();
                self.buffer.drain(..end + 2);
                return Ok(line);
            }
            if self.buffer.len() > MOST {
                return Err(io::Error::new(io::ErrorKind::InvalidData, "a response line too long"));
            }
            self.fill()?;
        }
    }

    fn exactly(&mut self, n: usize) -> io::Result<Vec<u8>> {
        if n > MOST {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "a literal too long"));
        }
        while self.buffer.len() < n {
            self.fill()?;
        }
        Ok(self.buffer.drain(..n).collect())
    }

    /// One response, its literals read into it.
    pub(crate) fn response(&mut self) -> io::Result<Vec<Piece>> {
        let mut pieces = Vec::new();
        loop {
            let line = String::from_utf8_lossy(&self.line()?).into_owned();
            match literal_length(&line) {
                Some(n) => {
                    pieces.push(Piece::Text(line));
                    pieces.push(Piece::Literal(self.exactly(n)?));
                }
                None => {
                    pieces.push(Piece::Text(line));
                    return Ok(pieces);
                }
            }
        }
    }

    pub(crate) fn send(&mut self, line: &[u8]) -> io::Result<()> {
        self.stream.write_all(line)?;
        self.stream.write_all(b"\r\n")?;
        self.stream.flush()
    }
}

/// The length a line's closing `{n}` announces.
fn literal_length(line: &str) -> Option<usize> {
    let open = line.strip_suffix('}')?.rfind('{')?;
    line[open + 1..line.len() - 1].parse().ok()
}
