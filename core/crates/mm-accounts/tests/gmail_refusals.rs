//! What a failed test of a login says when Gmail refused it, as tests/unit/accounts/test_gmail_refusals.py holds the
//! Python app's: each of Gmail's refusals (`mm_imap`'s classes) says what to do in a sentence of its own, whatever the
//! account's type; a refusal Gmail doesn't name says so of the App Password at a Gmail account, and what it always
//! said at any other (`quiet_for`). Each of them is a refusal the login hold holds back.

#![allow(clippy::unwrap_used)]

use std::sync::{Arc, Mutex};
use std::time::Duration;

use mm_accounts::connections::{Connections, GMAIL_REFUSED, quiet, quiet_for};
use mm_accounts::draft::Draft;
use mm_accounts::login_hold::refuses;
use mm_accounts::ports::{AccountChange, AccountEvents, ConfigFile, LoginProbe, LoginTrouble};
use mm_accounts::{Account, Directory};
use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Keychain, KeychainProblem, Moment, Secret};
use serde_json::{Map, Value, json};

const APP_PASSWORD: &str = "Gmail needs an App Password here, not your Google password. If Google offers none, \
                            Advanced Protection or a Workspace admin has switched them off.";
const WEB_SIGN_IN: &str =
    "Google wants you to sign in on the web first: sign in to Gmail in your browser, then test again.";
const IMAP_OFF: &str =
    "IMAP is switched off for this account: turn it on in Gmail's settings, or ask your Workspace admin to.";
const GOOGLES: [(&str, &str); 3] =
    [("LoginNeedsAppPassword", APP_PASSWORD), ("LoginNeedsWebSignIn", WEB_SIGN_IN), ("LoginImapOff", IMAP_OFF)];

#[test]
fn each_of_gmails_refusals_is_said_plainly_whatever_the_account_says_it_is() {
    for (class, says) in GOOGLES {
        assert_eq!(quiet(LoginTrouble(class)), says);
        for kind in [Some("gmail"), Some("imap"), None] {
            assert_eq!(quiet_for(LoginTrouble(class), kind), says, "{class} {kind:?}");
        }
        assert!(refuses(class), "{class}");
    }
}

#[test]
fn a_refusal_at_a_gmail_account_says_so_of_its_app_password_and_at_another_what_it_always_said() {
    assert_eq!(quiet_for(LoginTrouble("LoginRefused"), Some("gmail")), GMAIL_REFUSED);
    assert_eq!(GMAIL_REFUSED, "Gmail didn't accept that App Password: paste it again, or create a new one.");
    for kind in [Some("imap"), Some("proton"), None] {
        assert_eq!(quiet_for(LoginTrouble("LoginRefused"), kind), "AUTHENTICATIONFAILED", "{kind:?}");
    }
    for class in ["LoginUnavailable", "NoCredential", "Timeout"] {
        assert_eq!(quiet_for(LoginTrouble(class), Some("gmail")), quiet(LoginTrouble(class)), "{class}");
    }
}

#[derive(Debug)]
struct File(Mutex<Map<String, Value>>);

impl ConfigFile for File {
    fn read(&self) -> Result<Map<String, Value>, Fault> {
        Ok(self.0.lock().unwrap().clone())
    }

    fn write(&self, raw: &Map<String, Value>) -> Result<(), Fault> {
        *self.0.lock().unwrap() = raw.clone();
        Ok(())
    }
}

#[derive(Debug)]
struct NoKeychain;

impl Keychain for NoKeychain {
    fn find(&self, _item: &str) -> Result<Option<Secret>, KeychainProblem> {
        Ok(None)
    }

    fn put(&self, _item: &str, _secret: &Secret) -> Result<(), KeychainProblem> {
        Ok(())
    }

    fn delete(&self, _item: &str) -> Result<bool, KeychainProblem> {
        Ok(true)
    }
}

#[derive(Debug)]
struct At;

impl Clock for At {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000)
    }
}

/// A login that fails with the one class it was given.
#[derive(Debug)]
struct Refusing(&'static str);

impl LoginProbe for Refusing {
    fn saved(&self, _account: &Account, _timeout: Duration) -> Result<(), LoginTrouble> {
        Err(LoginTrouble(self.0))
    }

    fn draft(&self, _draft: &Draft, _timeout: Duration) -> Result<(), LoginTrouble> {
        Err(LoginTrouble(self.0))
    }
}

#[derive(Debug)]
struct Deaf;

impl AccountEvents for Deaf {
    fn publish(&self, _change: AccountChange) {}
}

fn connections(class: &'static str) -> Connections {
    let accounts = json!([
        {"email": "cy@example.test", "type": "gmail", "credential": {"keychain": "account:cy@example.test"}},
        {"email": "ada@example.test", "type": "imap", "host": "imap.example.test",
         "credential": {"keychain": "account:ada@example.test"}},
    ]);
    let Value::Object(raw) = json!({"accounts": accounts}) else { unreachable!() };
    let directory = Arc::new(Directory::new(Arc::new(File(Mutex::new(raw))), Arc::new(NoKeychain), Arc::new(At)));
    Connections::new(directory, Arc::new(Refusing(class)), Arc::new(Deaf), Arc::new(At))
}

fn said(connections: &Connections, asked: Value) -> Value {
    let Value::Object(asked) = asked else { unreachable!() };
    connections.test(&asked).unwrap()["error"].clone()
}

#[test]
fn a_test_of_a_saved_account_says_it_as_its_type_has_it() {
    assert_eq!(said(&connections("LoginRefused"), json!({"email": "cy@example.test"})), GMAIL_REFUSED);
    assert_eq!(said(&connections("LoginRefused"), json!({"email": "ada@example.test"})), "AUTHENTICATIONFAILED");
    for (class, says) in GOOGLES {
        assert_eq!(said(&connections(class), json!({"email": "cy@example.test"})), says, "{class}");
    }
}

#[test]
fn a_test_of_a_draft_says_it_as_its_type_has_it() {
    let draft = |kind: &str| {
        json!({"draft": {"email": "dee@example.test", "type": kind, "host": "imap.example.test",
                         "credential": {"style": "keychain", "value": "abcd efgh ijkl mnop"}}})
    };
    assert_eq!(said(&connections("LoginRefused"), draft("gmail")), GMAIL_REFUSED);
    assert_eq!(said(&connections("LoginRefused"), draft("imap")), "AUTHENTICATIONFAILED");
    let untyped = json!({"draft": {"email": "dee@example.test", "host": "imap.example.test",
                                   "credential": {"style": "keychain", "value": "secret"}}});
    assert_eq!(said(&connections("LoginRefused"), untyped), "AUTHENTICATIONFAILED");
}
