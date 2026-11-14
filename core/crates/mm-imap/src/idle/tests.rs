//! IDLE on the wire (tests/contract/test_mail_session_contract.py's "waiting for mail"): a change said ends it, its
//! time up ends it, DONE goes before anything else and the session takes a command after, and a connection dropped
//! or interrupted fails at once. The server is scripted on a plain connection, the session made straight over it.

#![allow(clippy::unwrap_used)]

use std::io::{BufRead, BufReader, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::mpsc;
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use mm_mailbox::ports::MailSession;
use mm_mailbox::session::MailServer;

use super::*;
use crate::session::Connection;
use crate::wire::Stream;

/// The connection's own time limit, which a wait in IDLE outlasts and gives back.
const LIMIT: Duration = Duration::from_secs(10);

/// The server's side of the connection.
struct Peer {
    reader: BufReader<TcpStream>,
    writer: TcpStream,
}

impl Peer {
    /// The next line the client sent; empty once it has gone, closed or reset.
    fn heard(&mut self) -> String {
        let mut line = String::new();
        match self.reader.read_line(&mut line) {
            Ok(_) => line.trim_end().to_owned(),
            Err(_) => String::new(),
        }
    }

    fn say(&mut self, line: &str) {
        self.writer.write_all(format!("{line}\r\n").as_bytes()).unwrap();
        self.writer.flush().unwrap();
    }
}

/// A session logged in to a server that `script` plays.
fn session(script: impl FnOnce(Peer) + Send + 'static) -> (Session, JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let server = std::thread::spawn(move || {
        let (tcp, _) = listener.accept().unwrap();
        let writer = tcp.try_clone().unwrap();
        script(Peer { reader: BufReader::new(tcp), writer });
    });
    let tcp = TcpStream::connect(("127.0.0.1", port)).unwrap();
    tcp.set_read_timeout(Some(LIMIT)).unwrap();
    (Session { connection: Connection::new(Stream::Plain(tcp)) }, server)
}

#[test]
fn a_change_said_ends_it_and_done_goes_before_anything_else() {
    let (mut s, server) = session(|mut peer| {
        assert_eq!(peer.heard(), "MM1 IDLE");
        peer.say("+ idling");
        // A keepalive, and a flag changed elsewhere: neither is a change to wait for.
        peer.say("* OK still here");
        peer.say("* 3 FETCH (FLAGS (\\Seen))");
        peer.say("* 4 EXISTS");
        assert_eq!(peer.heard(), "DONE");
        peer.say("* 1 RECENT");
        peer.say("MM1 OK IDLE terminated");
        assert_eq!(peer.heard(), "MM2 SELECT \"INBOX\"");
        peer.say("MM2 OK [READ-WRITE] selected");
    });
    assert_eq!(s.idle(Duration::from_secs(30)), Ok(true));
    // Out of IDLE, and the session still works, waiting as long as it did before.
    assert!(MailServer::select(&mut s, "INBOX", false).is_ok());
    assert_eq!(s.connection.wire.stream.read_timeout().unwrap(), Some(LIMIT));
    server.join().unwrap();
}

#[test]
fn with_nothing_new_it_ends_when_its_time_is_up() {
    let (mut s, server) = session(|mut peer| {
        assert_eq!(peer.heard(), "MM1 IDLE");
        peer.say("+ idling");
        assert_eq!(peer.heard(), "DONE");
        peer.say("MM1 OK IDLE terminated");
    });
    let began = Instant::now();
    assert_eq!(s.idle(Duration::from_millis(300)), Ok(false));
    // Past its time, and well short of the connection's limit.
    assert!(began.elapsed() >= Duration::from_millis(300) && began.elapsed() < LIMIT);
    assert_eq!(s.connection.wire.stream.read_timeout().unwrap(), Some(LIMIT));
    server.join().unwrap();
}

#[test]
fn a_change_said_before_the_server_idles_still_counts() {
    let (mut s, server) = session(|mut peer| {
        assert_eq!(peer.heard(), "MM1 IDLE");
        peer.say("* 2 EXPUNGE");
        peer.say("+ idling");
        assert_eq!(peer.heard(), "DONE");
        peer.say("MM1 OK IDLE terminated");
    });
    assert_eq!(s.idle(Duration::from_secs(30)), Ok(true));
    server.join().unwrap();
}

#[test]
fn a_server_that_ends_idle_itself_is_sent_no_done() {
    let (mut s, server) = session(|mut peer| {
        assert_eq!(peer.heard(), "MM1 IDLE");
        peer.say("+ idling");
        peer.say("MM1 OK IDLE ended");
        assert_eq!(peer.heard(), "MM2 SELECT \"INBOX\"");
        peer.say("MM2 OK selected");
    });
    assert_eq!(s.idle(Duration::from_secs(30)), Ok(false));
    assert!(MailServer::select(&mut s, "INBOX", false).is_ok());
    server.join().unwrap();
}

#[test]
fn a_server_without_idle_refuses_it() {
    let (mut s, server) = session(|mut peer| {
        assert_eq!(peer.heard(), "MM1 IDLE");
        peer.say("MM1 BAD unknown command");
    });
    assert_eq!(s.idle(Duration::from_secs(30)), Err(Error::Refused("IdleRefused")));
    server.join().unwrap();
}

#[test]
fn a_connection_dropped_in_idle_fails_at_once() {
    let (mut s, server) = session(|mut peer| {
        assert_eq!(peer.heard(), "MM1 IDLE");
        peer.say("+ idling");
    });
    let began = Instant::now();
    assert_eq!(s.idle(Duration::from_secs(60)), Err(Error::Unreachable("ConnectionLost")));
    assert!(began.elapsed() < LIMIT);
    server.join().unwrap();
}

#[test]
fn a_server_that_says_bye_in_idle_is_gone() {
    let (mut s, server) = session(|mut peer| {
        assert_eq!(peer.heard(), "MM1 IDLE");
        peer.say("+ idling");
        peer.say("* BYE shutting down");
    });
    assert_eq!(s.idle(Duration::from_secs(60)), Err(Error::Unreachable("Abort")));
    server.join().unwrap();
}

#[test]
fn an_interrupt_ends_it_at_once_and_closes_nothing() {
    let (idling, heard) = mpsc::channel();
    let (mut s, server) = session(move |mut peer| {
        assert_eq!(peer.heard(), "MM1 IDLE");
        peer.say("+ idling");
        idling.send(()).unwrap();
        // The client's side shut down: the server hears it go, and nothing more.
        assert_eq!(peer.heard(), "");
    });
    let interrupter = s.interrupter().unwrap();
    let waiting = std::thread::spawn(move || {
        let began = Instant::now();
        let said = s.idle(Duration::from_secs(60));
        (said, began.elapsed(), s)
    });
    heard.recv().unwrap();
    interrupter.interrupt();
    let (said, took, mut s) = waiting.join().unwrap();
    assert_eq!(said, Err(Error::Unreachable("ConnectionLost")));
    assert!(took < LIMIT);
    // The session is spent, and its own thread closes it: a logout sends nothing and fails nothing.
    MailSession::logout(&mut s);
    server.join().unwrap();
}

#[test]
fn only_exists_recent_and_expunge_say_the_folder_changed() {
    for line in ["* 4 EXISTS", "* 0 exists", "* 1 RECENT", "* 12 EXPUNGE"] {
        assert!(says_changed(line), "{line}");
    }
    for line in ["* OK still here", "* 3 FETCH (FLAGS (\\Seen))", "* EXISTS", "* x EXISTS", "+ idling", "MM1 OK"] {
        assert!(!says_changed(line), "{line}");
    }
}
