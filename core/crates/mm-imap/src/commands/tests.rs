//! The commands against a server that misbehaves: one that goes quiet, drops the connection part way through an
//! answer, refuses a command, says goodbye in the middle of one, or sends a message larger than most. The server is
//! scripted on a plain connection, the session made straight over it, as IDLE's tests make theirs.

#![allow(clippy::unwrap_used)]

use std::io::{BufRead, BufReader, Write};
use std::net::{TcpListener, TcpStream};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use mm_mailbox::session::{MailServer, ServerError};

use crate::session::{Connection, Session};
use crate::wire::Stream;

/// The connection's own time limit: how long a read waits for a server that says nothing.
const LIMIT: Duration = Duration::from_millis(400);

/// The server's side of the connection.
struct Peer {
    reader: BufReader<TcpStream>,
    writer: TcpStream,
}

impl Peer {
    /// The next line the client sent; empty once it has gone.
    fn heard(&mut self) -> String {
        let mut line = String::new();
        match self.reader.read_line(&mut line) {
            Ok(_) => line.trim_end().to_owned(),
            Err(_) => String::new(),
        }
    }

    fn say(&mut self, line: &str) {
        self.send(format!("{line}\r\n").as_bytes());
    }

    fn send(&mut self, bytes: &[u8]) {
        self.writer.write_all(bytes).unwrap();
        self.writer.flush().unwrap();
    }

    /// Wait until the client has gone, so the connection outlives what the client waits for.
    fn until_gone(mut self) {
        while !self.heard().is_empty() {}
    }
}

/// A session logged in to a server that `script` plays, each read waiting at most `LIMIT`.
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

/// A message of `size` bytes or a little more: a line of text and an attachment, base64 lines of 76.
fn message_of(size: usize) -> Vec<u8> {
    let mut raw = b"From: Lin <lin@example.test>\r\nTo: Ada <ada@example.test>\r\nSubject: Plans\r\n\
                    Message-ID: <plans@example.test>\r\nMIME-Version: 1.0\r\n\
                    Content-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\n\
                    The plans are attached.\r\n--b\r\nContent-Type: application/pdf; name=plans.pdf\r\n\
                    Content-Transfer-Encoding: base64\r\n\r\n"
        .to_vec();
    let line =
        [b"JVBERi0xLjQKJcfsj6IKNSAwIG9iago8PC9MZW5ndGggNiAwIFI+PgpzdHJlYW0KeJwr5HIK4TJQ".as_slice(), b"\r\n"].concat();
    while raw.len() < size {
        raw.extend_from_slice(&line);
    }
    raw.extend_from_slice(b"--b--\r\n");
    raw
}

#[test]
fn a_message_with_a_25_mb_attachment_is_read_whole() {
    // 25 MB attached is about 34 MB once encoded, as Gmail lets it be sent.
    let raw = message_of(34 << 20);
    let sent = raw.clone();
    let (mut s, server) = session(move |mut peer| {
        assert_eq!(peer.heard(), "MM1 UID FETCH 7 (BODY.PEEK[])");
        peer.send(format!("* 1 FETCH (UID 7 BODY[] {{{}}}\r\n", sent.len()).as_bytes());
        peer.send(&sent);
        peer.say(")");
        peer.say("MM1 OK done");
        peer.until_gone();
    });
    s.connection.wire.stream.set_read_timeout(Some(Duration::from_secs(30))).unwrap();
    assert_eq!(s.body(7).unwrap().map(|body| body.len()), Some(raw.len()));
    drop(s);
    server.join().unwrap();
}

#[test]
fn a_literal_larger_than_any_message_a_server_takes_gives_the_connection_up_before_it_is_read() {
    let (mut s, server) = session(|mut peer| {
        assert_eq!(peer.heard(), "MM1 UID FETCH 7 (BODY.PEEK[])");
        // Announced, and never sent: the client must not wait for it, nor make room for it.
        peer.say("* 1 FETCH (UID 7 BODY[] {4294967295}");
        peer.until_gone();
    });
    let began = Instant::now();
    assert_eq!(s.body(7), Err(ServerError::Unreachable("TlsOrProtocolError")));
    assert!(began.elapsed() < LIMIT, "it gave up at once, not after waiting for the bytes");
    drop(s);
    server.join().unwrap();
}

#[test]
fn a_server_that_goes_quiet_mid_answer_fails_within_the_connections_limit() {
    let (mut s, server) = session(|mut peer| {
        assert_eq!(peer.heard(), "MM1 UID FETCH 7 (BODY.PEEK[])");
        peer.say("* 1 FETCH (UID 7 BODY[] {100}");
        peer.send(b"From: Lin <lin@example.test>\r\n");
        // And then nothing, the connection still open.
        peer.until_gone();
    });
    let began = Instant::now();
    assert_eq!(s.body(7), Err(ServerError::Unreachable("Timeout")));
    assert!(began.elapsed() >= LIMIT && began.elapsed() < LIMIT * 10, "{:?}", began.elapsed());
    drop(s);
    server.join().unwrap();
}

#[test]
fn a_server_that_goes_quiet_before_its_answer_fails_within_the_connections_limit() {
    let (mut s, server) = session(|mut peer| {
        assert_eq!(peer.heard(), "MM1 UID MOVE 3 \"Archive\"");
        peer.until_gone();
    });
    let began = Instant::now();
    assert_eq!(s.uid_move(&[3], "Archive"), Err(ServerError::Unreachable("Timeout")));
    assert!(began.elapsed() < LIMIT * 10);
    drop(s);
    server.join().unwrap();
}

#[test]
fn a_connection_dropped_mid_fetch_is_lost_and_keeps_nothing_half_read() {
    let (mut s, server) = session(|mut peer| {
        assert_eq!(peer.heard(), "MM1 UID FETCH 7 (BODY.PEEK[])");
        peer.say("* 1 FETCH (UID 7 BODY[] {100}");
        peer.send(b"From: Lin <lin@example.test>\r\n");
    });
    assert_eq!(s.body(7), Err(ServerError::Unreachable("ConnectionLost")));
    server.join().unwrap();
}

#[test]
fn a_connection_dropped_mid_batch_of_openings_keeps_nothing_of_the_batch() {
    let (mut s, server) = session(|mut peer| {
        assert_eq!(peer.heard(), "MM1 UID FETCH 1,2 (UID RFC822.SIZE BODY.PEEK[]<0.65536>)");
        peer.say("* 1 FETCH (UID 1 RFC822.SIZE 5 BODY[]<0> {5}");
        peer.send(b"Hello)\r\n");
    });
    assert_eq!(s.openings(&[1, 2], 65536).err(), Some(ServerError::Unreachable("ConnectionLost")));
    server.join().unwrap();
}

#[test]
fn a_connection_dropped_mid_move_is_lost_not_refused() {
    // Lost, the move may or may not have happened: the command queue asks the server where the message is before it
    // tries again, where a refusal would give the message back.
    let (mut s, server) = session(|mut peer| {
        assert_eq!(peer.heard(), "MM1 UID MOVE 3 \"Archive\"");
        peer.say("* OK [COPYUID 7 3 9] moved");
    });
    assert_eq!(s.uid_move(&[3], "Archive"), Err(ServerError::Unreachable("ConnectionLost")));
    server.join().unwrap();
}

#[test]
fn a_bye_mid_command_is_the_connection_gone() {
    let (mut s, server) = session(|mut peer| {
        assert_eq!(peer.heard(), "MM1 SELECT \"INBOX\"");
        peer.say("* 3 EXISTS");
        peer.say("* BYE server shutting down");
        peer.until_gone();
    });
    assert_eq!(s.select("INBOX", false).err(), Some(ServerError::Unreachable("Abort")));
    drop(s);
    server.join().unwrap();
}

#[test]
fn a_no_or_bad_is_a_refusal_and_the_session_takes_the_next_command() {
    let (mut s, server) = session(|mut peer| {
        assert_eq!(peer.heard(), "MM1 SELECT \"Plans\"");
        peer.say("MM1 NO [NONEXISTENT] no such mailbox");
        assert_eq!(peer.heard(), "MM2 UID STORE 3 +FLAGS (\\Seen)");
        peer.say("MM2 BAD no mailbox selected");
        assert_eq!(peer.heard(), "MM3 SELECT \"INBOX\"");
        peer.say("* 2 EXISTS");
        peer.say("* OK [UIDVALIDITY 9] ok");
        peer.say("MM3 OK [READ-WRITE] selected");
        assert_eq!(peer.heard(), "MM4 UID MOVE 3 \"Archive\"");
        peer.say("MM4 NO [TRYCREATE] no such mailbox");
    });
    assert_eq!(s.select("Plans", false).err(), Some(ServerError::Refused("error")));
    assert_eq!(s.store_flags(&[3], true, &["\\Seen"]), Err(ServerError::Refused("error")));
    let selected = s.select("INBOX", false).unwrap();
    assert_eq!((selected.exists, selected.uidvalidity), (2, Some(9)));
    assert_eq!(s.uid_move(&[3], "Archive"), Err(ServerError::Refused("error")));
    server.join().unwrap();
}

#[test]
fn a_message_another_client_expunged_is_no_body_and_a_neighbours_still_comes() {
    let (mut s, server) = session(|mut peer| {
        // Gone before the FETCH: a server answers OK with nothing for it, or NO.
        assert_eq!(peer.heard(), "MM1 UID FETCH 7 (BODY.PEEK[])");
        peer.say("* 6 EXPUNGE");
        peer.say("MM1 OK nothing to fetch");
        assert_eq!(peer.heard(), "MM2 UID FETCH 8 (BODY.PEEK[])");
        peer.say("MM2 NO [EXPUNGEISSUED] some messages were expunged");
        assert_eq!(peer.heard(), "MM3 UID FETCH 1,2 (UID RFC822.SIZE BODY.PEEK[]<0.65536>)");
        peer.say("* 1 FETCH (UID 1 RFC822.SIZE 5 BODY[]<0> {5}");
        peer.send(b"Hello)\r\n");
        peer.say("MM3 NO Some messages could not be FETCHed (Failure)");
        // The one left out is asked for alone, and is still not there.
        assert_eq!(peer.heard(), "MM4 UID FETCH 2 (UID RFC822.SIZE BODY.PEEK[]<0.65536>)");
        peer.say("MM4 NO Some messages could not be FETCHed (Failure)");
    });
    assert_eq!(s.body(7), Ok(None));
    assert_eq!(s.body(8), Err(ServerError::Refused("error")));
    let openings = s.openings(&[1, 2], 65536).unwrap();
    assert_eq!(openings.keys().copied().collect::<Vec<_>>(), vec![1]);
    server.join().unwrap();
}

#[test]
fn a_header_line_of_a_megabyte_reads_whole_inside_its_literal() {
    let mut raw = b"From: Lin <lin@example.test>\r\nX-Long: ".to_vec();
    raw.extend(std::iter::repeat_n(b'a', 1 << 20));
    raw.extend_from_slice(b"\r\nSubject: Long\r\n\r\nShort.\r\n");
    let sent = raw.clone();
    let (mut s, server) = session(move |mut peer| {
        assert_eq!(peer.heard(), "MM1 UID FETCH 7 (BODY.PEEK[])");
        peer.send(format!("* 1 FETCH (UID 7 BODY[] {{{}}}\r\n", sent.len()).as_bytes());
        peer.send(&sent);
        peer.say(")");
        peer.say("MM1 OK done");
    });
    assert_eq!(s.body(7).unwrap(), Some(raw));
    server.join().unwrap();
}
