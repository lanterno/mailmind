//! The Proton Bridge's password, as an account whose credential is `{"proton_bridge": true}` logs in with it
//! (`bridge`, through the Directory), as tests/unit/accounts/test_secrets.py holds Python's `bridge_password`: the
//! environment's first, then 1Password's item, then the login Keychain's `proton-bridge-password`; a source that gives
//! nothing passing to the next; what is found kept for the process, and nothing found kept for none of it.

#![allow(clippy::unwrap_used)]

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use mm_accounts::Directory;
use mm_accounts::bridge::{BridgeGiven, ITEM};
use mm_accounts::one_password::OpReads;
use mm_accounts::ports::{ConfigFile, OnePassword};
use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Keychain, KeychainProblem, Moment, Secret};
use serde_json::{Map, Value, json};

const BEA: &str = "bea@example.test";
const REF: &str = "op://Private/Bridge/password";

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

/// The login Keychain, what it was asked for, or one that doesn't answer.
#[derive(Debug, Default)]
struct Kept {
    items: Mutex<HashMap<String, String>>,
    asked: Mutex<Vec<String>>,
    locked: bool,
}

impl Keychain for Kept {
    fn find(&self, item: &str) -> Result<Option<Secret>, KeychainProblem> {
        self.asked.lock().unwrap().push(item.to_owned());
        if self.locked {
            return Err(KeychainProblem);
        }
        Ok(self.items.lock().unwrap().get(item).map(|s| Secret::new(s.clone())))
    }
}

impl Kept {
    fn holding(password: &str) -> Arc<Kept> {
        let kept = Kept::default();
        kept.items.lock().unwrap().insert(ITEM.to_owned(), password.to_owned());
        Arc::new(kept)
    }

    fn keep(&self, password: &str) {
        self.items.lock().unwrap().insert(ITEM.to_owned(), password.to_owned());
    }

    fn asked(&self) -> Vec<String> {
        self.asked.lock().unwrap().clone()
    }
}

/// `op read`, printing what it was given for each reference, or failing; what it was asked, in order.
#[derive(Debug, Default)]
struct Op {
    prints: HashMap<String, Result<&'static str, Fault>>,
    asked: Mutex<Vec<String>>,
}

impl OnePassword for Op {
    fn read(&self, reference: &str) -> Result<Secret, Fault> {
        self.asked.lock().unwrap().push(reference.to_owned());
        self.prints[reference].map(|printed| Secret::new(printed.to_owned()))
    }
}

impl Op {
    fn printing(said: Result<&'static str, Fault>) -> Arc<Op> {
        Arc::new(Op { prints: HashMap::from([(REF.to_owned(), said)]), ..Op::default() })
    }

    fn asked(&self) -> Vec<String> {
        self.asked.lock().unwrap().clone()
    }
}

#[derive(Debug)]
struct At;

impl Clock for At {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000)
    }
}

fn given(password: Option<&str>, op_item: Option<&str>) -> BridgeGiven {
    BridgeGiven { password: password.map(|p| Secret::new(p.to_owned())), op_item: op_item.map(str::to_owned) }
}

/// A Directory holding Bea's Proton account, its secrets in `keychain`, and 1Password in `op` when there is one.
fn directory(keychain: &Arc<Kept>, op: Option<&Arc<Op>>) -> Directory {
    let Value::Object(raw) =
        json!({"accounts": [{"email": BEA, "type": "proton", "credential": {"proton_bridge": true}}]})
    else {
        unreachable!()
    };
    let directory = Directory::new(Arc::new(File(Mutex::new(raw))), keychain.clone(), Arc::new(At));
    match op {
        Some(op) => directory.reading_op(Arc::new(OpReads::new(op.clone()))),
        None => directory,
    }
}

fn password(directory: &Directory) -> Result<String, Fault> {
    let bea = directory.get(BEA).unwrap().unwrap();
    directory.password(&bea).map(|secret| secret.expose().to_owned())
}

#[test]
fn the_environments_password_comes_before_1password_and_the_keychain() {
    let keychain = Kept::holding("from-keychain");
    let op = Op::printing(Ok("from-op\n"));
    let directory = directory(&keychain, Some(&op)).reading_bridge(given(Some("from-env"), Some("Bridge")));
    assert_eq!(password(&directory).unwrap(), "from-env");
    assert!(op.asked().is_empty() && keychain.asked().is_empty());
}

#[test]
fn then_the_1password_item_the_environment_names_its_password_field_trimmed() {
    let keychain = Kept::holding("from-keychain");
    let op = Op::printing(Ok("  from-op \n"));
    let directory = directory(&keychain, Some(&op)).reading_bridge(given(None, Some("Bridge")));
    assert_eq!(password(&directory).unwrap(), "from-op");
    assert_eq!(op.asked(), [REF]);
    assert!(keychain.asked().is_empty());
}

#[test]
fn then_the_keychains_item_when_1password_gives_nothing() {
    for said in [Err(Fault::new("OpReadFailed")), Ok(" \n")] {
        let keychain = Kept::holding("from-keychain");
        let op = Op::printing(said);
        let directory = directory(&keychain, Some(&op)).reading_bridge(given(None, Some("Bridge")));
        assert_eq!(password(&directory).unwrap(), "from-keychain");
        assert_eq!((op.asked(), keychain.asked()), (vec![REF.to_owned()], vec![ITEM.to_owned()]));
    }
}

#[test]
fn an_empty_password_from_the_environment_is_none() {
    let keychain = Kept::holding("from-keychain");
    let directory = directory(&keychain, None).reading_bridge(given(Some(""), None));
    assert_eq!(password(&directory).unwrap(), "from-keychain");
}

#[test]
fn a_directory_the_environment_says_nothing_to_reads_the_keychains_item_alone() {
    let keychain = Kept::holding("from-keychain");
    let op = Op::printing(Ok("from-op"));
    let directory = directory(&keychain, Some(&op));
    assert_eq!(password(&directory).unwrap(), "from-keychain");
    // No item named, so 1Password isn't asked; and without a way to read 1Password the item named isn't read.
    assert!(op.asked().is_empty());
    let directory = self::directory(&keychain, None).reading_bridge(given(None, Some("Bridge")));
    assert_eq!(password(&directory).unwrap(), "from-keychain");
}

#[test]
fn what_is_found_is_kept_for_the_process() {
    let keychain = Kept::holding("first");
    let directory = directory(&keychain, None).reading_bridge(BridgeGiven::default());
    assert_eq!(password(&directory).unwrap(), "first");
    keychain.keep("second");
    assert_eq!(password(&directory).unwrap(), "first");
    assert_eq!(keychain.asked(), [ITEM]);
}

#[test]
fn none_anywhere_is_no_credential_and_nothing_is_kept_of_it() {
    let keychain = Arc::new(Kept::default());
    let op = Op::printing(Err(Fault::new("OpReadFailed")));
    let directory = directory(&keychain, Some(&op)).reading_bridge(given(None, Some("Bridge")));
    assert_eq!(password(&directory).unwrap_err(), Fault::new("NoCredential"));
    keychain.keep("kept-since");
    assert_eq!(password(&directory).unwrap(), "kept-since");
    assert_eq!(op.asked(), [REF, REF]);
}

#[test]
fn a_keychain_that_doesnt_answer_is_a_fault() {
    let keychain = Arc::new(Kept { locked: true, ..Kept::default() });
    let directory = directory(&keychain, None).reading_bridge(BridgeGiven::default());
    assert_eq!(password(&directory).unwrap_err(), Fault::new("KeychainProblem"));
}

#[test]
fn nothing_found_or_given_shows_in_a_debug() {
    // This stand-in prints what it holds: an empty one, so what shows is the Directory's own.
    let keychain = Arc::new(Kept::default());
    let directory = directory(&keychain, None).reading_bridge(given(Some("from-env"), Some("Bridge Item")));
    password(&directory).unwrap();
    let shown = format!("{directory:?} {:?}", given(Some("from-env"), Some("Bridge Item")));
    for secret in ["from-env", "Bridge Item"] {
        assert!(!shown.contains(secret), "{shown}");
    }
}
