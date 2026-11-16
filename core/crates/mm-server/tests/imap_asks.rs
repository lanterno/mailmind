//! What a session asks an IMAP server for a connection kept between jobs and for the body backfill (#5), against a
//! stand-in on loopback: whether the connection still answers (NOOP), how long it waits for each answer from now on,
//! messages' openings with each one's whole size, several a FETCH, and whole messages several a FETCH.

#![allow(clippy::unwrap_used)]

mod stand_in;

use std::time::{Duration, Instant};

use mm_imap::{Endpoint, Server, Session};
use mm_kernel::seams::Secret;
use mm_mailbox::session::{MailServer, Opening, ServerError};
use stand_in::{ADA, Imap, Loopback, PASSWORD, note, with_pdf};

const HEAD: u32 = 65_536;

fn session(imap: &Imap) -> Session {
    let at = Endpoint { host: "127.0.0.1", port: imap.port, starttls: false, loopback: true };
    let server = Server::connect(&Loopback, at, Duration::from_secs(10)).unwrap();
    server.login(ADA, &Secret::new(PASSWORD.to_owned())).unwrap()
}

/// Ada's Archive: a short note (1), a message with a PDF of about 100 KiB (2), and another note (3).
fn archive() -> Imap {
    let imap = Imap::start();
    imap.add(ADA, "Archive", [(1, note(1)), (2, with_pdf(2, "The plans, attached.", 100)), (3, note(3))]);
    imap
}

#[test]
fn openings_come_several_a_fetch_each_with_its_whole_size() {
    let imap = archive();
    let mut s = session(&imap);
    s.select("Archive", true).unwrap();
    let openings = s.openings(&[1, 2, 3], HEAD).unwrap();
    let fetches = imap.sent(ADA, "UID FETCH");
    assert_eq!(fetches.len(), 1, "{fetches:?}");
    assert!(fetches[0].ends_with("(UID RFC822.SIZE BODY.PEEK[]<0.65536>)"), "{fetches:?}");
    let short = &openings[&1];
    assert_eq!(short, &Opening { raw: note(1), size: Some(u32::try_from(note(1).len()).unwrap()) });
    assert!(short.is_whole(HEAD));
    let big = &openings[&2];
    let whole = with_pdf(2, "The plans, attached.", 100);
    assert_eq!(big.raw, whole[..65_536]);
    assert_eq!(big.size, Some(u32::try_from(whole.len()).unwrap()));
    assert!(!big.is_whole(HEAD), "cut off at 64 KiB");
    assert!(openings[&3].is_whole(HEAD));
}

#[test]
fn an_opening_whose_size_the_server_doesnt_say_is_whole_only_if_it_ended_short() {
    let imap = archive();
    imap.held().no_sizes = true;
    let mut s = session(&imap);
    s.select("Archive", true).unwrap();
    let openings = s.openings(&[1, 2], HEAD).unwrap();
    assert_eq!(openings[&1].size, None);
    assert!(openings[&1].is_whole(HEAD));
    assert!(!openings[&2].is_whole(HEAD));
    // An opening exactly as long as the message is whole however much was asked.
    let exact = Opening { raw: vec![b'x'; 10], size: Some(10) };
    assert!(exact.is_whole(10) && !Opening { raw: vec![b'x'; 10], size: None }.is_whole(10));
}

#[test]
fn whole_messages_come_several_a_fetch() {
    let imap = archive();
    let mut s = session(&imap);
    s.select("Archive", true).unwrap();
    let bodies = s.bodies(&[1, 2, 3]).unwrap();
    assert_eq!(bodies.keys().copied().collect::<Vec<_>>(), [1, 2, 3]);
    assert_eq!(bodies[&2], with_pdf(2, "The plans, attached.", 100));
    let fetches = imap.sent(ADA, "UID FETCH");
    assert_eq!(fetches.len(), 1, "{fetches:?}");
    assert!(fetches[0].ends_with("(UID BODY.PEEK[])"), "{fetches:?}");
    assert!(s.bodies(&[]).unwrap().is_empty());
}

#[test]
fn a_message_gone_from_the_server_is_left_out_of_both() {
    let imap = archive();
    let mut s = session(&imap);
    s.select("Archive", true).unwrap();
    assert_eq!(s.openings(&[1, 9], HEAD).unwrap().keys().copied().collect::<Vec<_>>(), [1]);
    assert_eq!(s.bodies(&[9, 3]).unwrap().keys().copied().collect::<Vec<_>>(), [3]);
}

#[test]
fn a_noop_says_whether_the_connection_still_answers() {
    let imap = archive();
    let mut s = session(&imap);
    assert_eq!(s.noop(), Ok(()));
    assert_eq!(imap.sent(ADA, "NOOP").len(), 1);
    // The server's idle timeout ends it, unannounced.
    imap.end_every_connection();
    assert!(matches!(s.noop(), Err(ServerError::Unreachable(_))));
}

#[test]
fn a_time_limit_set_holds_for_each_answer_from_then_on() {
    let imap = archive();
    let mut s = session(&imap);
    imap.held().slow_noops = 2;
    s.set_timeout(1);
    let asked = Instant::now();
    assert!(matches!(s.noop(), Err(ServerError::Unreachable(_))));
    assert!(asked.elapsed() < Duration::from_millis(2_500), "it waited {:?}", asked.elapsed());
    // A longer limit waits the slow answer out.
    let mut s = session(&imap);
    s.set_timeout(10);
    assert_eq!(s.noop(), Ok(()));
}
