//! The SMTP conversation against a scripted server, byte for byte as `smtplib` speaks it (each expected value is what
//! Python's `smtplib` sent to the same script), what each failure is called, and what `Server::connect` refuses
//! before any credential could go.

#![allow(clippy::unwrap_used)]

use std::cell::RefCell;
use std::collections::VecDeque;
use std::io::{self, Read, Write};
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, TcpListener};
use std::path::Path;
use std::rc::Rc;
use std::time::Duration;

use mm_kernel::seams::{Outside, Secret, Tripped};
use mm_net::Lost;
use mm_smtp::{Client, Endpoint, Error, Server};

/// One turn of a scripted server.
enum Step {
    /// What the client must write next.
    Hear(Vec<u8>),
    /// What the server says.
    Say(Vec<u8>),
    /// The server closes the connection: reads end, and writes fail.
    Close,
}

impl std::fmt::Debug for Step {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Step::Hear(bytes) => write!(f, "Hear({:?})", String::from_utf8_lossy(bytes)),
            Step::Say(bytes) => write!(f, "Say({:?})", String::from_utf8_lossy(bytes)),
            Step::Close => f.write_str("Close"),
        }
    }
}

fn hear(bytes: impl AsRef<[u8]>) -> Step {
    Step::Hear(bytes.as_ref().to_vec())
}

fn say(bytes: impl AsRef<[u8]>) -> Step {
    Step::Say(bytes.as_ref().to_vec())
}

#[derive(Debug, Default)]
struct Log {
    steps: VecDeque<Step>,
    /// What the client wrote that no step has heard yet.
    pending: Vec<u8>,
    /// Each write the client made, and each call of `before_dot`, in order.
    events: Vec<Event>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum Event {
    Wrote(Vec<u8>),
    BeforeDot,
}

/// A server that speaks only its script: a client that reads when it should write, or writes when it should read,
/// fails the test.
#[derive(Debug, Clone, Default)]
struct Script(Rc<RefCell<Log>>);

impl Script {
    fn new(steps: impl IntoIterator<Item = Step>) -> Script {
        let script = Script::default();
        script.0.borrow_mut().steps = steps.into_iter().collect();
        script
    }

    /// Every step taken, or only the close left.
    fn finished(&self) {
        let log = self.0.borrow();
        let left: Vec<&Step> = log.steps.iter().filter(|s| !matches!(s, Step::Close)).collect();
        assert!(left.is_empty(), "the script still had {left:?}");
        assert!(log.pending.is_empty(), "the client wrote {:?} unasked", String::from_utf8_lossy(&log.pending));
    }

    fn events(&self) -> Vec<Event> {
        self.0.borrow().events.clone()
    }

    fn before_dot(&self) -> impl FnMut() + use<> {
        let log = self.0.clone();
        move || log.borrow_mut().events.push(Event::BeforeDot)
    }
}

impl Read for Script {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        let mut log = self.0.borrow_mut();
        let (n, spent) = match log.steps.front_mut() {
            Some(Step::Say(bytes)) => {
                let n = bytes.len().min(buf.len());
                buf[..n].copy_from_slice(&bytes[..n]);
                bytes.drain(..n);
                (n, bytes.is_empty())
            }
            Some(Step::Close) | None => (0, false),
            Some(Step::Hear(expected)) => {
                panic!("the client read while the server waited to hear {:?}", String::from_utf8_lossy(expected))
            }
        };
        if spent {
            log.steps.pop_front();
        }
        Ok(n)
    }
}

impl Write for Script {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let mut log = self.0.borrow_mut();
        if matches!(log.steps.front(), Some(Step::Close)) {
            return Err(io::ErrorKind::BrokenPipe.into());
        }
        log.events.push(Event::Wrote(buf.to_vec()));
        log.pending.extend_from_slice(buf);
        while let Some(Step::Hear(expected)) = log.steps.front() {
            if log.pending.len() < expected.len() {
                break;
            }
            let n = expected.len();
            assert_eq!(
                String::from_utf8_lossy(&log.pending[..n]),
                String::from_utf8_lossy(expected),
                "the client wrote something else"
            );
            log.pending.drain(..n);
            log.steps.pop_front();
        }
        if !log.pending.is_empty() && !matches!(log.steps.front(), Some(Step::Hear(_))) {
            panic!("the client wrote {:?} when the server was to speak", String::from_utf8_lossy(&log.pending));
        }
        Ok(buf.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

const LOCAL: IpAddr = IpAddr::V4(Ipv4Addr::LOCALHOST);
const ADA: &str = "ada@example.test";
const EHLO: &str = "250-fake-smtp\r\n250-SIZE 52428800\r\n250-8BITMIME\r\n250 AUTH PLAIN LOGIN\r\n";
/// `\0ada@example.test\0app-password` in base64, as `smtplib` sends it.
const PLAIN: &str = "AUTH PLAIN AGFkYUBleGFtcGxlLnRlc3QAYXBwLXBhc3N3b3Jk\r\n";
const RAW: &[u8] = b"From: Ada <ada@example.test>\r\nSubject: Dots\r\n\r\n.one\r\nline\n.two\r\n..three\rx.four";

fn password() -> Secret {
    Secret::new("app-password".to_owned())
}

fn to(addresses: &[&str]) -> Vec<String> {
    addresses.iter().map(|a| (*a).to_owned()).collect()
}

/// The greeting, EHLO offering SIZE and AUTH PLAIN LOGIN, and a PLAIN login.
fn logged_in_steps() -> Vec<Step> {
    vec![say("220 ready\r\n"), hear("ehlo [127.0.0.1]\r\n"), say(EHLO), hear(PLAIN), say("235 ok\r\n")]
}

fn logged_in(script: &Script) -> Client<Script> {
    let mut client = Client::greet(script.clone(), LOCAL).unwrap();
    client.ehlo().unwrap();
    client.login(ADA, &password()).unwrap();
    client
}

fn scripted(steps: impl IntoIterator<Item = Step>) -> Script {
    Script::new(logged_in_steps().into_iter().chain(steps))
}

/// A send of `raw` to `envelope` over `script`, its outcome, the client then quitting.
fn sent(script: &Script, envelope: &[&str], raw: &[u8]) -> Result<(), Error> {
    let mut client = logged_in(script);
    let outcome = client.send(ADA, &to(envelope), raw, &mut script.before_dot());
    client.quit();
    outcome
}

#[test]
fn a_message_goes_as_smtplib_sends_it_dot_stuffed_with_its_final_dot_in_one_write() {
    let stuffed: &[u8] =
        b"From: Ada <ada@example.test>\r\nSubject: Dots\r\n\r\n..one\r\nline\n..two\r\n...three\rx.four\r\n.\r\n";
    let script = scripted([
        hear("mail from:<ada@example.test> size=78\r\n"),
        say("250 ok\r\n"),
        hear("rcpt to:<lin@example.test>\r\n"),
        say("250 ok\r\n"),
        hear("rcpt to:<noor@studio.example.test>\r\n"),
        say("250 ok\r\n"),
        hear("data\r\n"),
        say("354 go\r\n"),
        hear(stuffed),
        say("250 queued\r\n"),
        hear("quit\r\n"),
        say("221 bye\r\n"),
    ]);
    assert_eq!(sent(&script, &["lin@example.test", "noor@studio.example.test"], RAW), Ok(()));
    script.finished();
    let events = script.events();
    assert_eq!(events.iter().filter(|e| **e == Event::BeforeDot).count(), 1);
    let at = events.iter().position(|e| *e == Event::BeforeDot).unwrap();
    assert_eq!(events[at + 1], Event::Wrote(stuffed.to_vec()), "the message and its dot go in the write after it");
    assert_eq!(events[at - 1], Event::Wrote(b"data\r\n".to_vec()));
}

#[test]
fn a_message_ends_in_a_line_end_before_its_dot() {
    for (raw, stuffed) in [(&b""[..], &b"\r\n.\r\n"[..]), (b"a\r\n", b"a\r\n.\r\n"), (b".\r\n.", b"..\r\n..\r\n.\r\n")]
    {
        let script = scripted([
            hear("mail from:<ada@example.test> size=".to_owned() + &raw.len().to_string() + "\r\n"),
            say("250 ok\r\n"),
            hear("rcpt to:<lin@example.test>\r\n"),
            say("250 ok\r\n"),
            hear("data\r\n"),
            say("354 go\r\n"),
            hear(stuffed),
            say("250 queued\r\n"),
            hear("quit\r\n"),
            say("221 bye\r\n"),
        ]);
        assert_eq!(sent(&script, &["lin@example.test"], raw), Ok(()));
        script.finished();
    }
}

#[test]
fn login_goes_with_the_user_first_and_answers_each_challenge_with_the_password_as_smtplib_does() {
    let script = Script::new([
        say("220 ready\r\n"),
        hear("ehlo [127.0.0.1]\r\n"),
        say("250-fake\r\n250 AUTH LOGIN\r\n"),
        hear("AUTH LOGIN YWRhQGV4YW1wbGUudGVzdA==\r\n"),
        say("334 UGFzc3dvcmQ6\r\n"),
        hear("YXBwLXBhc3N3b3Jk\r\n"),
        say("235 ok\r\n"),
        // No SIZE offered, so none is given.
        hear("mail from:<ada@example.test>\r\n"),
        say("250 ok\r\n"),
        hear("rcpt to:<lin@example.test>\r\n"),
        say("250 ok\r\n"),
        hear("data\r\n"),
        say("354 go\r\n"),
        hear("Hi\r\n.\r\n"),
        say("250 queued\r\n"),
        hear("quit\r\n"),
        say("221 bye\r\n"),
    ]);
    assert_eq!(sent(&script, &["lin@example.test"], b"Hi\r\n"), Ok(()));
    script.finished();
}

#[test]
fn an_old_style_auth_line_offers_its_mechanisms_and_503_is_a_login() {
    let script = Script::new([
        say("220 ready\r\n"),
        hear("ehlo [127.0.0.1]\r\n"),
        say("250-fake\r\n250-AUTH=PLAIN\r\n250 SIZE\r\n"),
        hear(PLAIN),
        say("503 already\r\n"),
    ]);
    logged_in(&script);
    script.finished();
}

#[test]
fn a_refused_login_is_permanent_once_each_offered_mechanism_has_been_tried() {
    let script = Script::new([
        say("220 ready\r\n"),
        hear("ehlo [127.0.0.1]\r\n"),
        say(EHLO),
        hear(PLAIN),
        say("535 5.7.8 bad\r\n"),
        hear("AUTH LOGIN YWRhQGV4YW1wbGUudGVzdA==\r\n"),
        say("535 5.7.8 bad\r\n"),
    ]);
    let mut client = Client::greet(script.clone(), LOCAL).unwrap();
    client.ehlo().unwrap();
    let e = client.login(ADA, &password()).unwrap_err();
    script.finished();
    assert_eq!(e, Error::Auth(535));
    assert_eq!((e.said(), e.permanent(), e.unanswered(), e.unreachable()), ("auth failed", true, false, false));

    let script = Script::new([
        say("220 ready\r\n"),
        hear("ehlo [127.0.0.1]\r\n"),
        say("250-fake\r\n250 AUTH PLAIN\r\n"),
        hear(PLAIN),
        say("454 4.7.0 try later\r\n"),
    ]);
    let mut client = Client::greet(script.clone(), LOCAL).unwrap();
    client.ehlo().unwrap();
    let e = client.login(ADA, &password()).unwrap_err();
    assert_eq!((&e, e.permanent()), (&Error::Auth(454), false));
}

#[test]
fn a_login_with_no_mechanism_spoken_here_or_no_end_to_its_challenges_fails() {
    for offered in ["250-fake\r\n250 SIZE\r\n", "250-fake\r\n250 AUTH CRAM-MD5 plain\r\n"] {
        let script = Script::new([say("220 ready\r\n"), hear("ehlo [127.0.0.1]\r\n"), say(offered)]);
        let mut client = Client::greet(script.clone(), LOCAL).unwrap();
        client.ehlo().unwrap();
        let e = client.login(ADA, &password()).unwrap_err();
        assert_eq!((&e, e.said(), e.permanent()), (&Error::Failed, "send failed", false));
        script.finished();
    }

    // The initial response, then five answers, as `_MAXCHALLENGE` allows.
    let answer = &PLAIN["AUTH PLAIN ".len()..];
    let mut steps =
        vec![say("220 ready\r\n"), hear("ehlo [127.0.0.1]\r\n"), say("250-fake\r\n250 AUTH PLAIN\r\n"), hear(PLAIN)];
    for _ in 0..5 {
        steps.extend([say("334 \r\n"), hear(answer)]);
    }
    steps.push(say("334 \r\n"));
    let script = Script::new(steps);
    let mut client = Client::greet(script.clone(), LOCAL).unwrap();
    client.ehlo().unwrap();
    assert_eq!(client.login(ADA, &password()).unwrap_err(), Error::Failed);
    script.finished();
}

#[test]
fn a_password_or_user_outside_ascii_is_never_sent() {
    let script = Script::new([say("220 ready\r\n"), hear("ehlo [127.0.0.1]\r\n"), say(EHLO)]);
    let mut client = Client::greet(script.clone(), LOCAL).unwrap();
    client.ehlo().unwrap();
    let e = client.login(ADA, &Secret::new("p\u{e4}ssword".to_owned())).unwrap_err();
    assert_eq!((&e, e.said()), (&Error::Unsendable, "send failed"));
    assert_eq!(client.login("\u{e4}da@example.test", &password()).unwrap_err(), Error::Unsendable);
    script.finished();
}

#[test]
fn every_recipient_refused_is_a_refusal_permanent_only_when_each_is_a_5xx() {
    for (first, second, permanent) in [("451", "451", false), ("550", "553", true), ("550", "450", false)] {
        let script = scripted([
            hear("mail from:<ada@example.test> size=78\r\n"),
            say("250 ok\r\n"),
            hear("rcpt to:<lin@example.test>\r\n"),
            say(format!("{first} no\r\n")),
            hear("rcpt to:<noor@studio.example.test>\r\n"),
            say(format!("{second} no\r\n")),
            hear("rset\r\n"),
            say("250 reset\r\n"),
            hear("quit\r\n"),
            say("221 bye\r\n"),
        ]);
        let e = sent(&script, &["lin@example.test", "noor@studio.example.test"], RAW).unwrap_err();
        script.finished();
        let codes = vec![first.parse().unwrap(), second.parse().unwrap()];
        assert_eq!(e, Error::Recipients(codes));
        assert_eq!((e.said(), e.permanent(), e.unanswered()), ("recipient refused", permanent, false));
        assert!(!script.events().contains(&Event::BeforeDot));
    }
}

#[test]
fn some_recipients_refused_is_still_a_send() {
    let script = scripted([
        hear("mail from:<ada@example.test> size=4\r\n"),
        say("250 ok\r\n"),
        hear("rcpt to:<nobody@example.test>\r\n"),
        say("550 no\r\n"),
        hear("rcpt to:<lin@example.test>\r\n"),
        say("250 ok\r\n"),
        hear("data\r\n"),
        say("354 go\r\n"),
        hear("Hi\r\n.\r\n"),
        say("250 queued\r\n"),
        hear("quit\r\n"),
        say("221 bye\r\n"),
    ]);
    assert_eq!(sent(&script, &["nobody@example.test", "lin@example.test"], b"Hi\r\n"), Ok(()));
    script.finished();
}

#[test]
fn a_recipient_named_twice_and_refused_counts_once_as_smtplib_counts_it() {
    // Two of two refused, but one address: on to DATA, whose refusal sends no RSET.
    let script = scripted([
        hear("mail from:<ada@example.test> size=4\r\n"),
        say("250 ok\r\n"),
        hear("rcpt to:<lin@example.test>\r\n"),
        say("550 no\r\n"),
        hear("rcpt to:<lin@example.test>\r\n"),
        say("550 no\r\n"),
        hear("data\r\n"),
        say("554 no valid recipients\r\n"),
        hear("quit\r\n"),
        say("221 bye\r\n"),
    ]);
    let e = sent(&script, &["lin@example.test", "lin@example.test"], b"Hi\r\n").unwrap_err();
    script.finished();
    assert_eq!((&e, e.said(), e.permanent()), (&Error::Data(554), "rejected by server", true));
}

#[test]
fn a_421_to_a_recipient_lets_the_connection_go() {
    let script = scripted([
        hear("mail from:<ada@example.test> size=78\r\n"),
        say("250 ok\r\n"),
        hear("rcpt to:<a@example.test>\r\n"),
        say("550 no\r\n"),
        hear("rcpt to:<b@example.test>\r\n"),
        say("421 closing\r\n"),
    ]);
    let e = sent(&script, &["a@example.test", "b@example.test", "c@example.test"], RAW).unwrap_err();
    script.finished();
    assert_eq!((&e, e.permanent()), (&Error::Recipients(vec![550, 421]), false));
}

#[test]
fn a_refused_sender_is_a_recipient_refused() {
    let script = scripted([
        hear("mail from:<ada@example.test> size=78\r\n"),
        say("553 5.7.1 sender not allowed\r\n"),
        hear("rset\r\n"),
        say("250 reset\r\n"),
        hear("quit\r\n"),
        say("221 bye\r\n"),
    ]);
    let e = sent(&script, &["lin@example.test"], RAW).unwrap_err();
    script.finished();
    assert_eq!((&e, e.said(), e.permanent()), (&Error::Sender(553), "recipient refused", true));
}

#[test]
fn a_refusal_after_the_dot_is_an_answer_not_a_doubt() {
    for (code, permanent) in [(554, true), (452, false)] {
        let script = scripted([
            hear("mail from:<ada@example.test> size=4\r\n"),
            say("250 ok\r\n"),
            hear("rcpt to:<lin@example.test>\r\n"),
            say("250 ok\r\n"),
            hear("data\r\n"),
            say("354 go\r\n"),
            hear("Hi\r\n.\r\n"),
            say(format!("{code} 5.7.1 rejected\r\n")),
            hear("rset\r\n"),
            say("250 reset\r\n"),
            hear("quit\r\n"),
            say("221 bye\r\n"),
        ]);
        let e = sent(&script, &["lin@example.test"], b"Hi\r\n").unwrap_err();
        script.finished();
        assert_eq!(e, Error::Data(code));
        assert_eq!((e.said(), e.permanent(), e.unanswered()), ("rejected by server", permanent, false));
    }
}

#[test]
fn no_answer_after_the_whole_message_went_is_unanswered() {
    let script = scripted([
        hear("mail from:<ada@example.test> size=4\r\n"),
        say("250 ok\r\n"),
        hear("rcpt to:<lin@example.test>\r\n"),
        say("250 ok\r\n"),
        hear("data\r\n"),
        say("354 go\r\n"),
        hear("Hi\r\n.\r\n"),
        Step::Close,
    ]);
    let e = sent(&script, &["lin@example.test"], b"Hi\r\n").unwrap_err();
    script.finished();
    assert_eq!(e, Error::Unanswered);
    assert!(e.unanswered() && !e.permanent() && !e.unreachable());
    assert_eq!(e.said(), "connection lost after the message went out");
    assert_eq!(script.events().iter().filter(|e| **e == Event::BeforeDot).count(), 1);
}

#[test]
fn a_connection_lost_before_the_whole_message_went_is_only_lost() {
    let lost = |steps: Vec<Step>, called: usize| {
        let script = scripted(steps);
        let e = sent(&script, &["lin@example.test"], b"Hi\r\n").unwrap_err();
        script.finished();
        assert_eq!(e, Error::Disconnected);
        assert!(!e.unanswered() && !e.permanent() && e.unreachable());
        assert_eq!(e.said(), "connection lost");
        assert_eq!(script.events().iter().filter(|e| **e == Event::BeforeDot).count(), called);
    };
    lost(vec![hear("mail from:<ada@example.test> size=4\r\n"), Step::Close], 0);
    let to_data = || {
        vec![
            hear("mail from:<ada@example.test> size=4\r\n"),
            say("250 ok\r\n"),
            hear("rcpt to:<lin@example.test>\r\n"),
            say("250 ok\r\n"),
            hear("data\r\n"),
        ]
    };
    lost(to_data().into_iter().chain([Step::Close]).collect(), 0);
    // The write that holds the message and its dot fails: `before_dot` was called, and nothing went whole.
    lost(to_data().into_iter().chain([say("354 go\r\n"), Step::Close]).collect(), 1);
}

#[test]
fn multi_line_replies_are_read_whole() {
    let script = Script::new([
        say("220-hello\r\n220 ready\r\n"),
        hear("ehlo [127.0.0.1]\r\n"),
        say(EHLO),
        hear(PLAIN),
        say("235 ok\r\n"),
        hear("mail from:<ada@example.test> size=78\r\n"),
        say("250-one\r\n250 two\r\n"),
        hear("rcpt to:<lin@example.test>\r\n"),
        say("550-5.1.1 no\r\n550 5.1.1 such user\r\n"),
        hear("rset\r\n"),
        say("250 reset\r\n"),
        hear("quit\r\n"),
        say("221 bye\r\n"),
    ]);
    let e = sent(&script, &["lin@example.test"], RAW).unwrap_err();
    script.finished();
    assert_eq!(e, Error::Recipients(vec![550]));
}

#[test]
fn a_reply_with_no_code_is_minus_one_and_one_too_long_is_a_500() {
    let script = scripted([
        hear("mail from:<ada@example.test> size=78\r\n"),
        say("xyz what\r\n"),
        hear("rset\r\n"),
        say("250 reset\r\n"),
        hear("quit\r\n"),
        say("221 bye\r\n"),
    ]);
    let e = sent(&script, &["lin@example.test"], RAW).unwrap_err();
    script.finished();
    assert_eq!((&e, e.permanent()), (&Error::Sender(-1), false));

    // The connection is let go: not even QUIT follows.
    let script =
        scripted([hear("mail from:<ada@example.test> size=78\r\n"), say(format!("250 {}\r\n", "x".repeat(9000)))]);
    let e = sent(&script, &["lin@example.test"], RAW).unwrap_err();
    script.finished();
    assert_eq!((&e, e.said(), e.permanent()), (&Error::Response(500), "send failed", true));
}

#[test]
fn an_empty_envelope_or_an_address_with_a_line_break_sends_nothing() {
    let script = scripted([hear("mail from:<ada@example.test> size=78\r\n"), say("250 ok\r\n")]);
    let mut client = logged_in(&script);
    let e = client.send(ADA, &[], RAW, &mut script.before_dot()).unwrap_err();
    assert_eq!((&e, e.said(), e.permanent()), (&Error::NoRecipients, "no envelope recipients", false));
    let e = client.send(ADA, &to(&["lin@example.test\r\nRCPT TO:<x@example.test>"]), RAW, &mut || {}).unwrap_err();
    assert_eq!((&e, e.said(), e.permanent()), (&Error::Unsendable, "send failed", false));
    // MAIL went, and nothing after it: the script fails a write it doesn't expect.
    script.finished();
}

#[test]
fn a_greeting_other_than_220_is_a_refused_connection() {
    for (code, permanent) in [(554, true), (421, false)] {
        let script = Script::new([say(format!("{code} not now\r\n"))]);
        let e = Client::greet(script.clone(), LOCAL).unwrap_err();
        script.finished();
        assert_eq!(e, Error::Greeting(code));
        assert_eq!((e.said(), e.permanent(), e.unreachable()), ("connection refused", permanent, !permanent));
    }
    let e = Client::greet(Script::new([Step::Close]), LOCAL).unwrap_err();
    assert_eq!((&e, e.said()), (&Error::Disconnected, "connection lost"));
}

#[test]
fn a_server_that_offers_no_starttls_is_left_after_ehlo() {
    let script =
        Script::new([say("220 ready\r\n"), hear("ehlo [127.0.0.1]\r\n"), say("250-fake\r\n250 AUTH PLAIN LOGIN\r\n")]);
    let mut client = Client::greet(script.clone(), LOCAL).unwrap();
    client.ehlo().unwrap();
    let e = client.starttls().unwrap_err();
    script.finished();
    assert_eq!((&e, e.said(), e.permanent()), (&Error::Failed, "send failed", false));

    // EHLO refused: nothing is offered.
    let script = Script::new([say("220 ready\r\n"), hear("ehlo [127.0.0.1]\r\n"), say("502 no\r\n")]);
    let mut client = Client::greet(script.clone(), LOCAL).unwrap();
    client.ehlo().unwrap();
    assert_eq!(client.starttls().unwrap_err(), Error::Failed);
    script.finished();
}

#[test]
fn starttls_refused_is_its_code_and_bytes_after_its_220_are_never_read_as_tls() {
    let offered = || vec![say("220 ready\r\n"), hear("ehlo [127.0.0.1]\r\n"), say("250-fake\r\n250 STARTTLS\r\n")];
    let script = Script::new(offered().into_iter().chain([hear("STARTTLS\r\n"), say("454 4.7.0 not now\r\n")]));
    let mut client = Client::greet(script.clone(), LOCAL).unwrap();
    client.ehlo().unwrap();
    let e = client.starttls().unwrap_err();
    script.finished();
    assert_eq!((&e, e.said(), e.permanent()), (&Error::Response(454), "send failed", false));

    let script = Script::new(offered().into_iter().chain([hear("STARTTLS\r\n"), say("220 go\r\n250 forged\r\n")]));
    let mut client = Client::greet(script.clone(), LOCAL).unwrap();
    client.ehlo().unwrap();
    let e = client.starttls().unwrap_err();
    assert_eq!((&e, e.said()), (&Error::Tls(Lost("SpokeBeforeTls")), "tls error"));
}

#[test]
fn after_starttls_the_server_is_asked_again_what_it_offers() {
    let script = Script::new([
        say("220 ready\r\n"),
        hear("ehlo [127.0.0.1]\r\n"),
        say("250-fake\r\n250 STARTTLS\r\n"),
        hear("STARTTLS\r\n"),
        say("220 go\r\n"),
        hear("ehlo [127.0.0.1]\r\n"),
        say(EHLO),
        hear(PLAIN),
        say("235 ok\r\n"),
    ]);
    let mut client = Client::greet(script.clone(), LOCAL).unwrap();
    client.ehlo().unwrap();
    let stream = client.starttls().unwrap();
    let mut client = Client::after_starttls(stream, LOCAL);
    client.ehlo().unwrap();
    client.login(ADA, &password()).unwrap();
    script.finished();
}

#[test]
fn ehlo_names_the_connections_own_address_never_the_machine() {
    for (local, said) in [
        (IpAddr::V6(Ipv6Addr::LOCALHOST), "ehlo [IPv6:::1]\r\n"),
        (IpAddr::V6(Ipv4Addr::LOCALHOST.to_ipv6_mapped()), "ehlo [127.0.0.1]\r\n"),
    ] {
        let script = Script::new([say("220 ready\r\n"), hear(said), say("250 fake\r\n")]);
        Client::greet(script.clone(), local).unwrap().ehlo().unwrap();
        script.finished();
    }
}

#[test]
fn an_error_holds_no_word_the_server_said_nor_an_address() {
    let script = scripted([
        hear("mail from:<ada@example.test> size=78\r\n"),
        say("250 ok\r\n"),
        hear("rcpt to:<lin@example.test>\r\n"),
        say("550 5.1.1 lin@example.test: no such user here\r\n"),
        hear("rset\r\n"),
        say("250 reset\r\n"),
    ]);
    let mut client = logged_in(&script);
    let e = client.send(ADA, &to(&["lin@example.test"]), RAW, &mut || {}).unwrap_err();
    for shown in [e.to_string(), format!("{e:?}"), format!("{client:?}")] {
        for private in ["example.test", "such user", "app-password", "ada"] {
            assert!(!shown.contains(private), "{shown:?} holds {private:?}");
        }
    }
}

#[derive(Debug)]
struct Refusing;

impl Outside for Refusing {
    fn open(&self, _path: &Path) -> Result<(), Tripped> {
        Ok(())
    }

    fn reach(&self, host: &str) -> Result<(), Tripped> {
        Err(Tripped(host.to_owned()))
    }

    fn run(&self, _program: &str) -> Result<(), Tripped> {
        Ok(())
    }
}

#[derive(Debug)]
struct Open;

impl Outside for Open {
    fn open(&self, _path: &Path) -> Result<(), Tripped> {
        Ok(())
    }

    fn reach(&self, _host: &str) -> Result<(), Tripped> {
        Ok(())
    }

    fn run(&self, _program: &str) -> Result<(), Tripped> {
        Ok(())
    }
}

fn at(port: u16, starttls: bool) -> Endpoint<'static> {
    Endpoint { host: "127.0.0.1", port, starttls, loopback: true }
}

const QUICK: Duration = Duration::from_secs(5);

/// A loopback server that greets, answers the client's first line with `ehlo`, and then reads whatever comes, which
/// the test gets back.
fn serving(greeting: &'static str, ehlo: &'static str) -> (u16, std::thread::JoinHandle<Vec<u8>>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let serving = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        stream.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
        stream.write_all(greeting.as_bytes()).unwrap();
        let mut heard = Vec::new();
        let mut byte = [0u8];
        while !heard.ends_with(b"\n") && stream.read(&mut byte).unwrap_or(0) == 1 {
            heard.push(byte[0]);
        }
        stream.write_all(ehlo.as_bytes()).unwrap();
        let _ = stream.read_to_end(&mut heard);
        heard
    });
    (port, serving)
}

#[test]
fn a_server_off_this_machine_waits_for_verified_tls() {
    let remote = Endpoint { host: "smtp.example.test", port: 465, starttls: false, loopback: false };
    let e = Server::connect(&Open, remote, QUICK).unwrap_err();
    assert_eq!((&e, e.said()), (&Error::Tls(Lost("VerifiedTlsNotOnTheRustServerYet")), "tls error"));
}

#[test]
fn a_host_outside_refuses_is_never_reached() {
    let e = Server::connect(&Refusing, at(1, true), QUICK).unwrap_err();
    assert_eq!((&e, e.said(), e.unreachable()), (&Error::Tripped, "send failed", false));
}

#[test]
fn a_closed_port_is_a_refused_connection() {
    let port = TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port();
    let e = Server::connect(&Open, at(port, true), QUICK).unwrap_err();
    assert_eq!(e, Error::Connect(Lost("ConnectionRefused")));
    assert_eq!((e.said(), e.permanent(), e.unreachable()), ("connection refused", false, true));
}

#[test]
fn a_server_that_offers_no_starttls_hears_only_ehlo_with_the_connections_address() {
    let (port, serving) = serving("220 ready\r\n", "250-fake\r\n250 AUTH PLAIN LOGIN\r\n");
    assert_eq!(Server::connect(&Open, at(port, true), QUICK).unwrap_err(), Error::Failed);
    assert_eq!(String::from_utf8(serving.join().unwrap()).unwrap(), "ehlo [127.0.0.1]\r\n");
}
