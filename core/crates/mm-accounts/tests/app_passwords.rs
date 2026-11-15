//! A Gmail App Password typed as Google shows it, four groups of four letters with spaces between (`account::as_kept`),
//! as tests/unit/accounts/test_app_passwords.py holds the Python app's: kept, and tried, without its spaces at an
//! account that strips them, a Gmail account's by default. Any other password, and any password at an account that
//! doesn't strip spaces, is kept as it was typed.

#![allow(clippy::unwrap_used)]

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use mm_accounts::Directory;
use mm_accounts::account::as_kept;
use mm_accounts::draft::throwaway_account;
use mm_accounts::ports::ConfigFile;
use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Keychain, KeychainProblem, Moment, Secret};
use serde_json::{Map, Value, json};

const DEE: &str = "dee@example.test";
const DEES: &str = "account:dee@example.test";
const SHOWN: &str = "abcd efgh ijkl mnop";
const WHOLE: &str = "abcdefghijklmnop";

#[test]
fn an_app_password_as_google_shows_it_is_kept_without_its_spaces() {
    for (typed, kept) in [
        (SHOWN, WHOLE),
        ("ABCD EFGH IJKL MNOP", "ABCDEFGHIJKLMNOP"),
        (WHOLE, WHOLE),
        ("abcd\u{a0}efgh\u{a0}ijkl\u{a0}mnop", WHOLE), // a no-break space, as a copy from the page may bring
        ("ab cd ef gh ij kl mn op", WHOLE),
        ("abcd efgh ijkl mno", "abcd efgh ijkl mno"), // fifteen letters: not an App Password
        ("abcd efgh ijkl mnopq", "abcd efgh ijkl mnopq"), // seventeen
        ("abcd efgh ijkl mn0p", "abcd efgh ijkl mn0p"), // a digit
        ("abcd-efgh-ijkl-mnop", "abcd-efgh-ijkl-mnop"),
        ("abcd efgh ijkl mn\u{f6}p", "abcd efgh ijkl mn\u{f6}p"), // a letter outside ASCII
        ("abcd\tefgh ijkl mnop", "abcd\tefgh ijkl mnop"),         // a tab is no space Google shows
        ("correct horse battery staple", "correct horse battery staple"),
    ] {
        assert_eq!(as_kept(Secret::new(typed.to_owned()), true).expose(), kept, "{typed:?}");
    }
}

#[test]
fn an_account_that_keeps_spaces_keeps_them() {
    assert_eq!(as_kept(Secret::new(SHOWN.to_owned()), false).expose(), SHOWN);
}

#[derive(Debug, Default)]
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

#[derive(Debug, Default)]
struct Kept(Mutex<HashMap<String, String>>);

impl Keychain for Kept {
    fn find(&self, item: &str) -> Result<Option<Secret>, KeychainProblem> {
        Ok(self.0.lock().unwrap().get(item).map(|s| Secret::new(s.clone())))
    }

    fn put(&self, item: &str, secret: &Secret) -> Result<(), KeychainProblem> {
        self.0.lock().unwrap().insert(item.to_owned(), secret.expose().to_owned());
        Ok(())
    }

    fn delete(&self, item: &str) -> Result<bool, KeychainProblem> {
        Ok(self.0.lock().unwrap().remove(item).is_some())
    }
}

impl Kept {
    fn holds(&self) -> HashMap<String, String> {
        self.0.lock().unwrap().clone()
    }
}

#[derive(Debug)]
struct At;

impl Clock for At {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000)
    }
}

fn directory() -> (Directory, Arc<Kept>) {
    let keychain = Arc::new(Kept::default());
    (Directory::new(Arc::new(File::default()), keychain.clone(), Arc::new(At)), keychain)
}

fn asked(value: Value) -> Map<String, Value> {
    let Value::Object(map) = value else { unreachable!() };
    map
}

fn typed(password: &str) -> Value {
    json!({"style": "keychain", "value": password})
}

fn holding(password: &str) -> HashMap<String, String> {
    HashMap::from([(DEES.to_owned(), password.to_owned())])
}

#[test]
fn a_gmail_account_added_with_its_app_password_as_shown_keeps_it_whole() {
    let (directory, keychain) = directory();
    directory.add_account(&asked(json!({"email": DEE, "type": "gmail", "credential": typed(SHOWN)}))).unwrap();
    assert_eq!(keychain.holds(), holding(WHOLE));
    let account = directory.get(DEE).unwrap().unwrap();
    assert_eq!(directory.password(&account).unwrap().expose(), WHOLE);
}

#[test]
fn an_imap_account_keeps_its_password_as_typed() {
    let (directory, keychain) = directory();
    let entry = json!({"email": DEE, "type": "imap", "host": "imap.example.test", "credential": typed(SHOWN)});
    directory.add_account(&asked(entry)).unwrap();
    assert_eq!(keychain.holds(), holding(SHOWN));
}

#[test]
fn an_account_says_whether_it_strips_spaces() {
    let (directory, keychain) = directory();
    let entry = json!({"email": DEE, "type": "gmail", "strip_spaces": false, "credential": typed(SHOWN)});
    directory.add_account(&asked(entry)).unwrap();
    assert_eq!(keychain.holds(), holding(SHOWN));
    directory.update_account(DEE, &asked(json!({"strip_spaces": true, "credential": typed(SHOWN)}))).unwrap();
    assert_eq!(keychain.holds(), holding(WHOLE));
}

#[test]
fn an_edit_that_makes_an_account_gmail_keeps_its_new_password_whole() {
    let (directory, keychain) = directory();
    let entry = json!({"email": DEE, "type": "imap", "host": "imap.example.test", "credential": typed("before")});
    directory.add_account(&asked(entry)).unwrap();
    directory.update_account(DEE, &asked(json!({"type": "gmail", "credential": typed(SHOWN)}))).unwrap();
    assert_eq!(keychain.holds(), holding(WHOLE));
}

#[test]
fn a_draft_tries_its_password_as_it_would_be_kept() {
    for (draft, tried) in [
        (json!({"type": "gmail"}), WHOLE),
        (json!({"type": "gmail", "strip_spaces": false}), SHOWN),
        (json!({"type": "imap", "host": "imap.example.test"}), SHOWN),
        (json!({"type": "imap", "host": "imap.example.test", "strip_spaces": true}), WHOLE),
    ] {
        let mut draft = asked(draft);
        draft.insert("email".into(), json!(DEE));
        draft.insert("credential".into(), typed(SHOWN));
        let tried_as = throwaway_account(&draft).unwrap();
        assert_eq!(tried_as.password.expose(), tried, "{draft:?}");
    }
}
