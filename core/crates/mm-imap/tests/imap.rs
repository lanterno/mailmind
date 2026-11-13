//! LIST's answer read as folders, a folder name sent back as an argument, and what a connection refuses before any
//! credential could go.

#![allow(clippy::unwrap_used)]

use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::Path;
use std::time::Duration;

use mm_imap::{Endpoint, Error, Server, gmail_labels, imap_quote, label_name, mailbox_arg, parse_list};
use mm_kernel::seams::{Outside, Tripped};

fn listed(lines: &[&str]) -> Vec<(String, Option<String>)> {
    let responses: Vec<(String, Option<Vec<u8>>)> = lines.iter().map(|l| ((*l).to_owned(), None)).collect();
    parse_list(&responses).into_iter().map(|f| (f.name, f.delimiter)).collect()
}

#[test]
fn list_lines_are_folders_with_their_names_as_the_server_wrote_them() {
    assert_eq!(
        listed(&[
            r#"(\HasNoChildren) "/" "INBOX""#,
            r#"(\HasNoChildren \Sent) "/" "[Gmail]/Sent Mail""#,
            r#"() "." Archive"#,
            r#"(\Noselect) NIL "Quoted \"name\"""#,
            r#"() "/" """#,
            "not a list line",
        ]),
        [
            ("INBOX".to_owned(), Some("/".to_owned())),
            ("[Gmail]/Sent Mail".to_owned(), Some("/".to_owned())),
            ("Archive".to_owned(), Some(".".to_owned())),
            (r#"Quoted \"name\""#.to_owned(), None),
        ]
    );
    let sent = &parse_list(&[(r#"(\HasNoChildren \Sent) "/" "Sent""#.to_owned(), None)])[0];
    assert!(sent.attributes.contains("\\sent") && sent.attributes.contains("\\hasnochildren"));
}

#[test]
fn a_literal_name_is_kept_in_lists_quoted_form_unless_it_breaks_a_line() {
    let responses = vec![
        (r#"() "/" {9}"#.to_owned(), Some(br#"Odd "one""#.to_vec())),
        (r#"() "/" {5}"#.to_owned(), Some(b"two\r\n".to_vec())),
    ];
    let names: Vec<String> = parse_list(&responses).into_iter().map(|f| f.name).collect();
    assert_eq!(names, [r#"Odd \"one\""#]);
}

#[test]
fn a_folder_name_goes_back_only_as_a_quoted_string_it_cannot_end() {
    assert_eq!(mailbox_arg("INBOX").as_deref(), Some(r#""INBOX""#));
    assert_eq!(mailbox_arg(r#"Quoted \"name\""#).as_deref(), Some(r#""Quoted \"name\"""#));
    for unsendable in ["", "a\r\nb", "bare \" quote", "trailing \\", "nul\0"] {
        assert_eq!(mailbox_arg(unsendable), None, "{unsendable:?}");
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

#[test]
fn a_host_outside_refuses_is_never_reached() {
    assert_eq!(Server::connect(&Refusing, at(1, true), QUICK).unwrap_err(), Error::Unreachable("Tripped"));
    let remote = Endpoint { host: "imap.example.test", port: 993, starttls: false, loopback: false };
    assert_eq!(Server::connect(&Refusing, remote, QUICK).unwrap_err(), Error::Unreachable("Tripped"));
}

#[test]
fn a_closed_port_is_out_of_reach() {
    let port = TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port();
    assert_eq!(Server::connect(&Open, at(port, true), QUICK).unwrap_err(), Error::Unreachable("ConnectionRefused"));
}

/// A server that greets with `greeting` and then reads whatever comes, which the test gets back.
fn greeting(greeting: &'static str) -> (u16, std::thread::JoinHandle<Vec<u8>>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let serving = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        stream.write_all(greeting.as_bytes()).unwrap();
        stream.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
        let mut heard = Vec::new();
        let _ = stream.read_to_end(&mut heard);
        heard
    });
    (port, serving)
}

#[test]
fn a_server_that_offers_no_starttls_is_left_before_anything_is_sent() {
    let (port, serving) = greeting("* OK [CAPABILITY IMAP4rev1 AUTH=PLAIN] ready\r\n");
    assert_eq!(Server::connect(&Open, at(port, true), QUICK).unwrap_err(), Error::Unreachable("Abort"));
    assert_eq!(serving.join().unwrap(), b"");
}

#[test]
fn a_server_that_says_bye_is_left() {
    let (port, serving) = greeting("* BYE too busy\r\n");
    assert_eq!(Server::connect(&Open, at(port, true), QUICK).unwrap_err(), Error::Unreachable("Abort"));
    assert_eq!(serving.join().unwrap(), b"");
}

#[test]
fn any_other_value_goes_quoted_unless_it_holds_a_line_break_or_nul() {
    assert_eq!(imap_quote("<a@example.test>").as_deref(), Some(r#""<a@example.test>""#));
    assert_eq!(imap_quote(r#"<b'A"B'\c@example.test>"#).as_deref(), Some(r#""<b'A\"B'\\c@example.test>""#));
    for unsendable in ["<a\r\n@example.test>", "<a\n@example.test>", "nul\0"] {
        assert_eq!(imap_quote(unsendable), None, "{unsendable:?}");
    }
}

#[test]
fn gmail_labels_go_as_a_list_with_system_ones_unquoted() {
    assert_eq!(gmail_labels(&["\\Inbox"]).as_deref(), Some("(\\Inbox)"));
    assert_eq!(
        gmail_labels(&["\\Inbox", "Work/Plans", "\\Not a system"]).as_deref(),
        Some(r#"(\Inbox "Work/Plans" "\\Not a system")"#)
    );
    assert_eq!(gmail_labels(&["two\r\nlines"]), None);
}

#[test]
fn a_label_is_read_unescaped_and_decoded() {
    assert_eq!(label_name("\\Important"), "\\Important");
    assert_eq!(label_name(r#"say \"hi\""#), r#"say "hi""#);
    assert_eq!(label_name("Caf&AOk-"), "Caf\u{e9}");
    assert_eq!(label_name("Tom &- Jerry"), "Tom & Jerry");
    assert_eq!(label_name("&Jjo-!"), "\u{263a}!");
    assert_eq!(label_name("broken &***-"), "broken &***-");
}
