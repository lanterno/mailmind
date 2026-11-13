//! Why a send didn't go, as the Python app tells it apart: `smtplib`'s exceptions, `Unanswered`, and what
//! `SmtpTransport.permanent` and `sanitize_error` make of each. An error holds a server's codes, never its words, an
//! address or a password.

use std::fmt;

use mm_net::Lost;

/// Why a send didn't go, by the exception the Python app would have caught.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Error {
    /// The envelope named no one, so nothing was sent: the Python app's `ValueError("no envelope recipients")`.
    NoRecipients,
    /// `Outside` refused the host, so it was never reached.
    Tripped,
    /// The TCP connection didn't go: the host's name didn't resolve, nothing listened, or it timed out.
    Connect(Lost),
    /// TLS didn't start: none for this host, the handshake failed, or the server spoke before it began
    /// (`ssl.SSLError`).
    Tls(Lost),
    /// The server greeted with a code other than 220 (`SMTPConnectError`).
    Greeting(i32),
    /// The connection was lost or closed before the whole message went out, or no answer came to a command
    /// (`SMTPServerDisconnected`).
    Disconnected,
    /// The whole message and its final dot went out and no answer came, so it may have been delivered
    /// (`compose.domain.errors.Unanswered`).
    Unanswered,
    /// AUTH was refused, the last mechanism's code (`SMTPAuthenticationError`).
    Auth(i32),
    /// MAIL FROM was refused (`SMTPSenderRefused`).
    Sender(i32),
    /// Every recipient was refused, or one with 421: each refused one's code (`SMTPRecipientsRefused`).
    Recipients(Vec<i32>),
    /// DATA, or the message after its final dot, was refused (`SMTPDataError`).
    Data(i32),
    /// Any other coded answer: STARTTLS refused, or a reply line too long, as 500 (`SMTPResponseException`).
    Response(i32),
    /// What `smtplib` raises as a bare `SMTPException`: no STARTTLS or AUTH offered, no mechanism spoken here, or
    /// AUTH's challenges never ending.
    Failed,
    /// A value that can't go in a command: a line break in it, or a character outside ASCII (`ValueError`).
    Unsendable,
}

impl Error {
    /// `SmtpTransport.permanent`: the server's answer refuses for good, every code it gave a 5xx. A dropped connection
    /// or a timeout had no answer, and may pass.
    pub fn permanent(&self) -> bool {
        match self {
            Error::Recipients(codes) => refused_for_good(codes),
            Error::Greeting(code)
            | Error::Auth(code)
            | Error::Sender(code)
            | Error::Data(code)
            | Error::Response(code) => refused_for_good(&[*code]),
            _ => false,
        }
    }

    /// `SmtpTransport.sanitize_error`: fixed words that never echo a recipient, the banner or AUTH's state.
    pub fn said(&self) -> &'static str {
        match self {
            Error::NoRecipients => "no envelope recipients",
            Error::Auth(_) => "auth failed",
            Error::Sender(_) | Error::Recipients(_) => "recipient refused",
            Error::Data(_) => "rejected by server",
            Error::Unanswered => "connection lost after the message went out",
            Error::Disconnected => "connection lost",
            Error::Greeting(_) => "connection refused",
            Error::Tls(_) => "tls error",
            Error::Connect(Lost(class)) => match *class {
                "ConnectionRefused" => "connection refused",
                "NameNotResolved" => "host not found",
                "Timeout" => "timeout",
                _ => "send failed",
            },
            Error::Tripped | Error::Response(_) | Error::Failed | Error::Unsendable => "send failed",
        }
    }

    /// `Unanswered`: the whole message went out and no answer came, so it may have been delivered, and sent again it
    /// could reach its recipients twice.
    pub fn unanswered(&self) -> bool {
        matches!(self, Error::Unanswered)
    }

    /// Whether asking again later may help: a connection that didn't go, was lost before the whole message went out,
    /// or was turned away for now. An unanswered message isn't, since sent again it may go twice.
    pub fn unreachable(&self) -> bool {
        match self {
            Error::Connect(_) | Error::Tls(_) | Error::Disconnected => true,
            Error::Greeting(code) => !refused_for_good(&[*code]),
            _ => false,
        }
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.said())
    }
}

impl std::error::Error for Error {}

/// `outbox_item.refused_for_good`: whether a server's answer, one code or one for each recipient, refuses for good,
/// every one of them a 5xx.
fn refused_for_good(codes: &[i32]) -> bool {
    !codes.is_empty() && codes.iter().all(|code| (500..600).contains(code))
}
