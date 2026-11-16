//! Waiting on a connection as long as a protocol says (IMAP's IDLE waits for minutes, where a command's answer waits
//! for the connection's time limit), and ending such a wait from another thread.
//!
//! Another thread may only interrupt a connection, never close it: closing a socket the connection's own thread may be
//! about to read frees its descriptor's number for the next socket opened, which that thread would then read. An
//! `Interrupter` holds a descriptor of its own for the same socket, and shuts the socket down through it, which wakes a
//! read blocked on it and closes nothing. The connection's thread closes its own.

use std::io;
use std::net::{Shutdown, TcpStream};
use std::time::Duration;

use crate::Stream;

impl Stream {
    fn tcp(&self) -> &TcpStream {
        match self {
            Stream::Plain(tcp) => tcp,
            Stream::Tls(tls) => &tls.sock,
        }
    }

    /// How long a read waits before it fails as timed out; None for ever.
    pub fn read_timeout(&self) -> io::Result<Option<Duration>> {
        self.tcp().read_timeout()
    }

    /// Wait at most `timeout` on each read from now on; None for ever. A zero is refused, as the system refuses it.
    pub fn set_read_timeout(&self, timeout: Option<Duration>) -> io::Result<()> {
        self.tcp().set_read_timeout(timeout)
    }

    /// How long a write waits before it fails as timed out from now on; None for ever. A zero is refused, as the system
    /// refuses it.
    pub fn set_write_timeout(&self, timeout: Option<Duration>) -> io::Result<()> {
        self.tcp().set_write_timeout(timeout)
    }

    /// What ends a read waiting on this connection, from another thread.
    pub fn interrupter(&self) -> io::Result<Interrupter> {
        Ok(Interrupter(self.tcp().try_clone()?))
    }

    /// Ask the system to probe the connection once it has been quiet for `after`, every `every` after that, and to
    /// call it lost after `tries` probes unanswered: so a wait of many minutes on a connection that a sleep or a
    /// change of network left dead fails within minutes, not when the wait runs out.
    pub fn keep_alive(&self, after: Duration, every: Duration, tries: u32) -> io::Result<()> {
        let probes = socket2::TcpKeepalive::new().with_time(after).with_interval(every).with_retries(tries);
        socket2::SockRef::from(self.tcp()).set_tcp_keepalive(&probes)
    }

    /// How long the connection may be quiet before the system probes it; None when it never does.
    pub fn keep_alive_after(&self) -> io::Result<Option<Duration>> {
        let socket = socket2::SockRef::from(self.tcp());
        if !socket.keepalive()? {
            return Ok(None);
        }
        socket.tcp_keepalive_time().map(Some)
    }
}

/// Whether an I/O error is a read that waited out its time limit, which asks nothing of the connection.
pub fn timed_out(e: &io::Error) -> bool {
    matches!(e.kind(), io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock)
}

/// Ends a wait on a connection from another thread: its socket shut down both ways, through a descriptor of the
/// interrupter's own. The connection's reads find it ended; nothing is closed.
#[derive(Debug)]
pub struct Interrupter(TcpStream);

impl Interrupter {
    pub fn interrupt(&self) {
        // A socket already shut down, or gone, has nothing left to wake.
        let _ = self.0.shutdown(Shutdown::Both);
    }
}
