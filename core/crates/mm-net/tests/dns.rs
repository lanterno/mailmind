//! A domain's mail servers, asked of a nameserver (`dns`), as tests/unit/accounts/test_mail_hosts.py holds the Python
//! app's: the question as RFC 1035 writes it, the answer read with its names' compression followed and no further than
//! it goes, and nameservers asked in turn. A nameserver is a thread on loopback, or bytes made here.

#![allow(clippy::unwrap_used)]

use std::net::{SocketAddr, UdpSocket};
use std::thread::JoinHandle;
use std::time::Duration;

use mm_net::dns::{Answer, answer, mail_hosts, nameservers, query};

const ACME: &str = "acme.example.test";

#[test]
fn resolv_conf_names_the_nameservers_in_its_order() {
    let text = "# a comment naming nameserver 10.0.0.9
search example.test
nameserver 192.0.2.53
nameserver   2001:db8::53
nameserver fe80::1%en0
nameserver not-an-address
nameserver
; nameserver 192.0.2.99
options ndots:2
nameserver 198.51.100.53 # the last
";
    let named: Vec<String> = nameservers(text).iter().map(SocketAddr::to_string).collect();
    assert_eq!(named, ["192.0.2.53:53", "[2001:db8::53]:53", "198.51.100.53:53"]);
}

#[test]
fn a_question_asks_for_the_domains_mx_records_with_recursion() {
    let asked = query(0x1234, ACME).unwrap();
    let mut expected = vec![0x12, 0x34, 0x01, 0x00, 0, 1, 0, 0, 0, 0, 0, 0];
    expected.extend_from_slice(b"\x04acme\x07example\x04test\x00\x00\x0f\x00\x01");
    assert_eq!(asked, expected);
}

#[test]
fn no_question_carries_what_isnt_a_domain() {
    let long = format!("{}.test", "a".repeat(64));
    let too_long = format!("{}.test", ["a".repeat(63), "a".repeat(63), "a".repeat(63), "a".repeat(63)].join("."));
    for domain in ["", "a..test", long.as_str(), "b\u{fc}cher.test", too_long.as_str()] {
        assert_eq!(query(1, domain), None, "{domain:?}");
    }
}

fn name(labels: &[&str]) -> Vec<u8> {
    let mut out = Vec::new();
    for label in labels {
        out.push(u8::try_from(label.len()).unwrap());
        out.extend_from_slice(label.as_bytes());
    }
    out.push(0);
    out
}

fn question() -> Vec<u8> {
    let mut out = name(&["acme", "example", "test"]);
    out.extend_from_slice(&[0, 15, 0, 1]);
    out
}

fn reply_to(ident: u16, flags: u16, question: &[u8], records: &[Vec<u8>], count: u16) -> Vec<u8> {
    let mut out = Vec::new();
    for field in [ident, flags, 1, count, 0, 0] {
        out.extend_from_slice(&field.to_be_bytes());
    }
    out.extend_from_slice(question);
    for record in records {
        out.extend_from_slice(record);
    }
    out
}

fn reply(flags: u16, records: &[Vec<u8>]) -> Vec<u8> {
    reply_to(7, flags, &question(), records, u16::try_from(records.len()).unwrap())
}

const AT_QUESTION: [u8; 2] = [0xC0, 0x0C];

fn record(kind: u16, class: u16, data: &[u8]) -> Vec<u8> {
    let mut out = AT_QUESTION.to_vec();
    out.extend_from_slice(&kind.to_be_bytes());
    out.extend_from_slice(&class.to_be_bytes());
    out.extend_from_slice(&300u32.to_be_bytes());
    out.extend_from_slice(&u16::try_from(data.len()).unwrap().to_be_bytes());
    out.extend_from_slice(data);
    out
}

fn mx(preference: u16, host: &[u8]) -> Vec<u8> {
    let mut out = preference.to_be_bytes().to_vec();
    out.extend_from_slice(host);
    out
}

fn hosts(found: &[&str]) -> Answer {
    Answer::Hosts(found.iter().map(|h| (*h).to_owned()).collect())
}

#[test]
fn an_answer_names_each_mx_hosts_in_its_order() {
    let data = reply(
        0x8180,
        &[
            record(15, 1, &mx(10, &name(&["aspmx", "l", "google", "com"]))),
            record(15, 1, &mx(20, &name(&["Alt1", "ASPMX", "l", "google", "com"]))),
        ],
    );
    assert_eq!(answer(7, ACME, &data), hosts(&["aspmx.l.google.com", "alt1.aspmx.l.google.com"]));
}

#[test]
fn a_name_compressed_into_one_before_it_is_read_whole() {
    let at_first = 12 + question().len() + 12 + 2; // the first record's host, after its owner, fields and preference
    let mut alt1 = b"\x04alt1".to_vec();
    alt1.extend_from_slice(&(0xC000 | u16::try_from(at_first).unwrap()).to_be_bytes());
    let mut mail = b"\x04mail".to_vec();
    mail.extend_from_slice(&(0xC000u16 | 12).to_be_bytes());
    let data = reply(
        0x8180,
        &[
            record(15, 1, &mx(10, &name(&["aspmx", "l", "google", "com"]))),
            record(15, 1, &mx(20, &alt1)),
            record(15, 1, &mx(30, &mail)),
        ],
    );
    let found = hosts(&["aspmx.l.google.com", "alt1.aspmx.l.google.com", "mail.acme.example.test"]);
    assert_eq!(answer(7, ACME, &data), found);
}

#[test]
fn records_of_other_kinds_are_passed_over() {
    let data = reply(
        0x8180,
        &[
            record(5, 1, &name(&["mail", "example", "test"])),
            record(15, 3, &mx(10, &name(&["mx", "example", "test"]))),
            record(15, 1, &mx(10, &name(&["aspmx", "l", "google", "com"]))),
        ],
    );
    assert_eq!(answer(7, ACME, &data), hosts(&["aspmx.l.google.com"]));
}

#[test]
fn a_domain_with_no_mx_records_or_none_at_all_names_none() {
    for flags in [0x8183, 0x8180] {
        assert_eq!(answer(7, ACME, &reply(flags, &[])), hosts(&[]), "{flags:#x}");
    }
}

#[test]
fn a_nameserver_that_says_it_failed_is_passed_over() {
    for flags in [0x8182, 0x8185, 0x8184] {
        assert_eq!(answer(7, ACME, &reply(flags, &[])), Answer::Failed, "{flags:#x}");
    }
}

#[test]
fn what_isnt_the_answer_to_the_question_is_not_read_as_one() {
    let q = question();
    let mut other_type = q.clone();
    let at = other_type.len() - 3;
    other_type[at] = 1;
    let mut other_domain = name(&["other", "example", "test"]);
    other_domain.extend_from_slice(&[0, 15, 0, 1]);
    for data in [
        reply_to(8, 0x8180, &q, &[], 0),                // another question's
        reply_to(7, 0x0100, &q, &[], 0),                // a question, not an answer
        reply_to(7, 0x8180, &other_domain, &[], 0),     // another domain's
        reply_to(7, 0x8180, &other_type, &[], 0),       // another record type's
        reply_to(7, 0x8180, &q[..q.len() - 1], &[], 0), // cut short
        reply(0x8180, &[])[..11].to_vec(),
        Vec::new(),
    ] {
        assert_eq!(answer(7, ACME, &data), Answer::Foreign, "{data:?}");
    }
}

#[test]
fn an_answer_cut_short_names_the_hosts_it_holds_whole() {
    let whole = record(15, 1, &mx(10, &name(&["aspmx", "l", "google", "com"])));
    let second = record(15, 1, &mx(20, &name(&["alt1", "aspmx", "l", "google", "com"])));
    let cut = second[..second.len() - 3].to_vec();
    assert_eq!(answer(7, ACME, &reply(0x8180, &[whole.clone(), cut])), hosts(&["aspmx.l.google.com"]));
    assert_eq!(answer(7, ACME, &reply_to(7, 0x8180, &question(), &[whole], 5)), hosts(&["aspmx.l.google.com"]));
}

#[test]
fn a_name_that_cant_be_read_ends_the_answer() {
    let at_itself = 12 + question().len() + 12 + 2;
    let mut long = Vec::new();
    for _ in 0..4 {
        long.push(63);
        long.extend_from_slice(&[b'a'; 63]);
    }
    long.push(0);
    for host in [
        (0xC000 | u16::try_from(at_itself).unwrap()).to_be_bytes().to_vec(), // a pointer to itself
        b"\x04mail\xc0".to_vec(),                                            // a pointer cut short
        b"\x04ma".to_vec(),                                                  // a label past the data
        b"\x44mail\x00".to_vec(),                                            // a label of a kind no answer uses
        b"\x04m\xc3\xa9l\x00".to_vec(),                                      // not ASCII
        long,                                                                // longer than a name can be
    ] {
        assert_eq!(answer(7, ACME, &reply(0x8180, &[record(15, 1, &mx(10, &host))])), hosts(&[]), "{host:?}");
    }
}

/// How a nameserver on loopback answers the one question it is asked.
#[derive(Clone, Copy)]
enum Answers {
    /// With these hosts.
    With(&'static [&'static str]),
    /// That it failed.
    Failing,
    /// Not at all.
    Silent,
    /// First with another question's id, then as `With`.
    ForeignFirst(&'static [&'static str]),
}

/// A nameserver on loopback that answers one question, and the thread that does.
fn nameserver(answers: Answers) -> (SocketAddr, JoinHandle<bool>) {
    let socket = UdpSocket::bind("127.0.0.1:0").unwrap();
    socket.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
    let address = socket.local_addr().unwrap();
    let thread = std::thread::spawn(move || {
        let mut buffer = [0u8; 512];
        let Ok((read, from)) = socket.recv_from(&mut buffer) else { return false };
        let asked = &buffer[..read];
        let ident = u16::from_be_bytes([asked[0], asked[1]]);
        let q = asked[12..].to_vec();
        let with = |found: &[&str]| {
            let records: Vec<Vec<u8>> =
                found.iter().map(|host| record(15, 1, &mx(10, &name(&host.split('.').collect::<Vec<_>>())))).collect();
            reply_to(ident, 0x8180, &q, &records, u16::try_from(records.len()).unwrap())
        };
        match answers {
            Answers::With(found) => drop(socket.send_to(&with(found), from)),
            Answers::Failing => drop(socket.send_to(&reply_to(ident, 0x8182, &q, &[], 0), from)),
            Answers::Silent => {}
            Answers::ForeignFirst(found) => {
                let other = reply_to(ident.wrapping_add(1), 0x8180, &q, &[], 0);
                drop(socket.send_to(&other, from));
                drop(socket.send_to(&with(found), from));
            }
        }
        true
    });
    (address, thread)
}

const QUICK: Duration = Duration::from_millis(300);

#[test]
fn a_domains_mail_servers_are_asked_of_the_nameserver() {
    let (address, asked) = nameserver(Answers::With(&["aspmx.l.google.com"]));
    assert_eq!(mail_hosts(&[address], ACME, 0x4242, QUICK), ["aspmx.l.google.com"]);
    assert!(asked.join().unwrap());
}

#[test]
fn an_answer_to_another_question_is_waited_past() {
    let (address, _) = nameserver(Answers::ForeignFirst(&["aspmx.l.google.com"]));
    assert_eq!(mail_hosts(&[address], ACME, 0x4242, QUICK), ["aspmx.l.google.com"]);
}

#[test]
fn a_nameserver_silent_or_failing_is_passed_over_for_the_next() {
    let (silent, silent_asked) = nameserver(Answers::Silent);
    let (failing, failing_asked) = nameserver(Answers::Failing);
    let (answering, answering_asked) = nameserver(Answers::With(&["aspmx.l.google.com"]));
    assert_eq!(mail_hosts(&[silent, failing, answering], ACME, 9, QUICK), ["aspmx.l.google.com"]);
    assert!(silent_asked.join().unwrap() && failing_asked.join().unwrap() && answering_asked.join().unwrap());
}

#[test]
fn no_nameserver_or_none_answering_names_none() {
    assert!(mail_hosts(&[], ACME, 9, QUICK).is_empty());
    let (silent, _) = nameserver(Answers::Silent);
    assert!(mail_hosts(&[silent], ACME, 9, QUICK).is_empty());
    assert!(mail_hosts(&[silent], "a..test", 9, QUICK).is_empty());
}
