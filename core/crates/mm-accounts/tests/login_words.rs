//! `mm_accounts::login_words`: what a person is told of a login that didn't work, by its class and the provider it
//! was tried at, and the page that helps. Every sentence says what to do next; none holds what a server said.

use mm_accounts::connections::{GMAIL_APP_PASSWORD, GMAIL_IMAP_OFF, GMAIL_REFUSED, quiet};
use mm_accounts::draft::{BAD_PORT, BAD_SMTP_PORT};
use mm_accounts::login_words::{Said, Whose, said_of};
use mm_accounts::ports::LoginTrouble;
use mm_accounts::presets::{GOOGLE_APP_PASSWORDS, PROTON_BRIDGE};

fn at(kind: &'static str, host: Option<&'static str>, email: &'static str) -> Whose<'static> {
    Whose { kind: Some(kind), host, email: Some(email) }
}

fn imap(email: &'static str) -> Whose<'static> {
    at("imap", Some("imap.example.test"), email)
}

#[test]
fn a_refused_password_at_a_provider_that_takes_app_passwords_sends_to_its_page() {
    for (whose, name, url) in [
        (at("imap", Some("imap.mail.me.com"), "ada@example.test"), "iCloud", "https://account.apple.com"),
        (imap("ada@icloud.com"), "iCloud", "https://account.apple.com"),
        (imap("ada@yahoo.co.uk"), "Yahoo", "https://login.yahoo.com/account/security"),
        (
            at("imap", Some("imap.fastmail.com"), "ada@example.test"),
            "Fastmail",
            "https://www.fastmail.help/hc/en-us/articles/360058752854",
        ),
    ] {
        let said = said_of("AUTHENTICATIONFAILED", whose, false);
        assert!(said.says.starts_with(&format!("{name} didn't accept that password.")), "{}", said.says);
        assert!(said.says.contains("app password"), "{}", said.says);
        assert_eq!(said.link.map(|l| l.url), Some(url));
    }
}

#[test]
fn a_refused_password_anywhere_else_says_to_check_it() {
    let said = said_of("AUTHENTICATIONFAILED", imap("ada@example.test"), false);
    assert_eq!(said.link, None);
    assert!(said.says.starts_with("The server didn't accept that address and password."));
}

#[test]
fn microsofts_accounts_hear_that_they_need_its_sign_in() {
    let said = said_of("AUTHENTICATIONFAILED", at("imap", Some("outlook.office365.com"), "ada@outlook.com"), false);
    assert!(said.says.contains("Microsoft's own sign-in"), "{}", said.says);
}

#[test]
fn gmails_refusals_keep_their_sentences_and_the_app_password_ones_send_to_google() {
    let gmail = at("gmail", None, "cy@gmail.com");
    assert_eq!(
        said_of(GMAIL_REFUSED, gmail, false),
        Said { says: GMAIL_REFUSED.into(), link: Some(GOOGLE_APP_PASSWORDS) }
    );
    assert_eq!(said_of(GMAIL_APP_PASSWORD, gmail, false).link, Some(GOOGLE_APP_PASSWORDS));
    assert_eq!(said_of(GMAIL_IMAP_OFF, gmail, false), Said { says: GMAIL_IMAP_OFF.into(), link: None });
}

#[test]
fn a_bridge_that_refuses_a_connection_isnt_running() {
    let proton = at("proton", Some("127.0.0.1"), "pia@proton.me");
    let said = said_of(quiet(LoginTrouble("ConnectionRefused")), proton, false);
    assert!(said.says.starts_with("Proton Bridge isn't running."), "{}", said.says);
    assert_eq!(said.link, Some(PROTON_BRIDGE));
    let said = said_of("AUTHENTICATIONFAILED", proton, false);
    assert!(said.says.contains("the password Bridge shows"), "{}", said.says);
}

#[test]
fn a_server_that_didnt_answer_says_to_check_its_name_and_port() {
    for class in ["ConnectionRefused", "Timeout", "NameNotResolved"] {
        let said = said_of(quiet(LoginTrouble(class)), imap("ada@example.test"), false);
        assert!(said.says.contains("Check the server's name and port"), "{class}: {}", said.says);
    }
}

#[test]
fn a_certificate_not_trusted_says_no_password_was_sent() {
    let said = said_of(quiet(LoginTrouble("CertificateNotVerified")), imap("ada@example.test"), false);
    assert!(said.says.starts_with("The server's certificate isn't trusted, so MailMind didn't send your password."));
    let said = said_of(quiet(LoginTrouble("TLSPinMismatch")), imap("ada@example.test"), false);
    assert!(said.says.contains("trust the new one in Settings"), "{}", said.says);
}

#[test]
fn the_outgoing_servers_trouble_says_reading_mail_works() {
    for class in ["LoginRefused", "ConnectionRefused", "CertificateNotVerified", "TlsError", "SMTPServerDisconnected"] {
        let said = said_of(quiet(LoginTrouble(class)), at("proton", Some("127.0.0.1"), "pia@proton.me"), true);
        assert!(said.says.starts_with("Reading mail works, but"), "{class}: {}", said.says);
    }
}

#[test]
fn a_port_no_connection_could_reach_says_what_a_port_is() {
    assert_eq!(said_of(BAD_PORT, imap("ada@example.test"), false).says, "The port must be a number from 1 to 65535.");
    assert!(said_of(BAD_SMTP_PORT, imap("ada@example.test"), false).says.starts_with("The outgoing server's port"));
}

#[test]
fn anything_else_says_what_to_check() {
    let said = said_of("ConnectionLost", imap("ada@example.test"), false);
    assert_eq!(said.says, "MailMind couldn't log in. Check the address, the password and the server's settings.");
}
