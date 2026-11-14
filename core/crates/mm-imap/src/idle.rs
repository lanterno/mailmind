//! IDLE (RFC 2177), as IDLE's workers wait on an account's INBOX (`MailSession.idle` and `interrupt`): the server
//! says when the selected folder changes, and DONE leaves IDLE before anything else is sent.
//!
//! A wait lasts as long as it was given, each read limited to what is left of it, and the connection's own time
//! limit comes back as it leaves. Another thread ends a wait through the session's `Interrupter`, which shuts the
//! socket down and closes nothing: the wait then fails as a lost connection, and the session is spent.

use std::sync::Arc;
use std::time::{Duration, Instant};

use mm_mailbox::idle_supervisor::Interrupt;
use mm_mailbox::ports::MailSession;
use mm_mailbox::realtime_ports::IdleSession;
use mm_mailbox::session::{MailServer, Selected, ServerError};

use crate::list::strip_prefix_ignoring_case;
use crate::session::{Error, Session};
use crate::wire::Piece;

/// What a server says when the selected folder changed, as the Python app counts it (`_CHANGES`).
const CHANGES: [&str; 3] = ["EXISTS", "RECENT", "EXPUNGE"];

/// Whether an untagged response says the folder changed: `* n EXISTS`, `* n RECENT` or `* n EXPUNGE`. A flag
/// changed elsewhere (`* n FETCH`) or a keepalive (`* OK`) is no change.
fn says_changed(line: &str) -> bool {
    let mut words = line.split_whitespace();
    words.next() == Some("*")
        && words.next().is_some_and(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()))
        && words.next().is_some_and(|said| CHANGES.iter().any(|c| said.eq_ignore_ascii_case(c)))
}

fn lost(e: &std::io::Error) -> Error {
    mm_net::lost(e).into()
}

/// What one response's first line says, as IDLE reads it.
enum Heard {
    /// The answer to the command tagged so: whether it was OK.
    Tagged(bool),
    /// `+`: the server idles.
    Idling,
    /// `* BYE`: the server is leaving.
    Bye,
    /// Any other untagged response: whether it says the folder changed.
    Untagged(bool),
}

fn heard(response: &[Piece], tag: &str) -> Result<Heard, Error> {
    let Some(Piece::Text(first)) = response.first() else {
        return Err(Error::Unreachable("ProtocolError"));
    };
    if let Some(rest) = first.strip_prefix(tag).and_then(|r| r.strip_prefix(' ')) {
        let status = rest.split_whitespace().next().unwrap_or("");
        return Ok(Heard::Tagged(status.eq_ignore_ascii_case("OK")));
    }
    if first.starts_with('+') {
        return Ok(Heard::Idling);
    }
    if strip_prefix_ignoring_case(first, "* BYE").is_some() {
        return Ok(Heard::Bye);
    }
    Ok(Heard::Untagged(says_changed(first)))
}

/// What a tagged answer to IDLE comes to: the server ended it with an OK, or refused it.
fn ended(ok: bool, changed: bool) -> Result<bool, Error> {
    if ok { Ok(changed) } else { Err(Error::Refused("IdleRefused")) }
}

impl Session {
    /// IDLE on the selected folder for at most `wait`: whether the server said it changed. DONE is sent, and the
    /// server's answer to IDLE read, before it returns, so the session takes a command next. A connection lost or
    /// interrupted fails as unreachable; a server that refuses IDLE, as refused.
    pub fn idle(&mut self, wait: Duration) -> Result<bool, Error> {
        let deadline = Instant::now() + wait;
        let tag = self.connection.next_tag();
        self.connection.wire.send(format!("{tag} IDLE").as_bytes()).map_err(|e| lost(&e))?;
        let mut changed = false;
        loop {
            let response = self.connection.wire.response().map_err(|e| lost(&e))?;
            match heard(&response, &tag)? {
                Heard::Idling => break,
                Heard::Tagged(ok) => return ended(ok, changed),
                Heard::Bye => return Err(Error::Unreachable("Abort")),
                Heard::Untagged(said) => changed |= said,
            }
        }
        let kept = self.connection.wire.stream.read_timeout().map_err(|e| lost(&e))?;
        let waited = self.wait(deadline, &tag, changed);
        // The connection's own limit again, for DONE's answer and what follows; on a connection that is gone, there
        // is nothing to set.
        let _ = self.connection.wire.stream.set_read_timeout(kept);
        let changed = match waited? {
            Waited::Over(changed) => changed,
            Waited::Ended(answer) => return answer,
        };
        self.connection.wire.send(b"DONE").map_err(|e| lost(&e))?;
        loop {
            let response = self.connection.wire.response().map_err(|e| lost(&e))?;
            match heard(&response, &tag)? {
                Heard::Tagged(ok) => return ended(ok, changed),
                Heard::Bye => return Err(Error::Unreachable("Abort")),
                Heard::Idling | Heard::Untagged(_) => {}
            }
        }
    }

    /// Idling: read what the server says until it says the folder changed, or until `deadline`.
    fn wait(&mut self, deadline: Instant, tag: &str, mut changed: bool) -> Result<Waited, Error> {
        while !changed {
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                break;
            }
            self.connection.wire.stream.set_read_timeout(Some(left)).map_err(|e| lost(&e))?;
            match self.connection.wire.response() {
                Ok(response) => match heard(&response, tag)? {
                    // The server ended IDLE itself: there is nothing to send DONE to.
                    Heard::Tagged(ok) => return Ok(Waited::Ended(ended(ok, changed))),
                    Heard::Bye => return Err(Error::Unreachable("Abort")),
                    Heard::Idling => {}
                    Heard::Untagged(said) => changed = said,
                },
                Err(e) if mm_net::timed_out(&e) => {}
                Err(e) => return Err(lost(&e)),
            }
        }
        Ok(Waited::Over(changed))
    }

    /// What ends a wait on this session from another thread, closing nothing.
    pub fn interrupter(&self) -> Result<mm_net::Interrupter, Error> {
        self.connection.wire.stream.interrupter().map_err(|e| lost(&e))
    }
}

/// How a wait in IDLE ended.
enum Waited {
    /// Its time was up, or the server said the folder changed (whether it did): DONE comes next.
    Over(bool),
    /// The server answered IDLE on its own.
    Ended(Result<bool, Error>),
}

/// The interrupter, as IDLE's workers hold one.
#[derive(Debug)]
struct Interrupting(mm_net::Interrupter);

impl Interrupt for Interrupting {
    fn interrupt(&self) {
        self.0.interrupt();
    }
}

impl IdleSession for Session {
    fn capabilities(&self) -> Vec<String> {
        MailServer::capabilities(self)
    }

    fn select(&mut self, folder: &str, readonly: bool) -> Result<Selected, ServerError> {
        MailServer::select(self, folder, readonly)
    }

    fn idle(&mut self, wait: Duration) -> Result<bool, ServerError> {
        Ok(Session::idle(self, wait)?)
    }

    fn interrupter(&self) -> Result<Arc<dyn Interrupt>, ServerError> {
        Ok(Arc::new(Interrupting(Session::interrupter(self)?)))
    }

    fn logout(&mut self) {
        MailSession::logout(self);
    }
}

#[cfg(test)]
mod tests;
