//! The secrets a person keeps in the login Keychain from Settings beside each account's own password, as
//! tests/unit/accounts/test_kept_secrets.py holds the Python app's: the Claude API key, kept in its item and named in
//! the file one version on, and taken out of both; the Proton Bridge's password, kept in its item, the one a login
//! found let go, and each account that logs in with it told it changed. A secret the Keychain can't hold is refused in
//! its words, a Keychain that won't is `Unreachable`, and either leaves the file as it was.

#![allow(clippy::unwrap_used)]

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use mm_accounts::bridge::ITEM;
use mm_accounts::connections::Connections;
use mm_accounts::draft::Draft;
use mm_accounts::one_password::OpReads;
use mm_accounts::ports::{AccountChange, AccountEvents, ConfigFile, LoginProbe, LoginTrouble, OnePassword};
use mm_accounts::{Account, Directory};
use mm_kernel::errors::{Failure, Fault, Kind, Problem};
use mm_kernel::seams::{Clock, Keychain, KeychainProblem, Moment, Secret};
use serde_json::{Map, Value, json};

const KEY_ITEM: &str = "anthropic-api-key";
const UNPRINTABLE: &str = "A secret MailMind keeps in the Keychain is printable ASCII, and not empty: letters, digits, punctuation and spaces.";
const OP_KEY: &str = "op://Private/Claude/credential";

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

impl File {
    fn holding(raw: Value) -> Arc<File> {
        let Value::Object(raw) = raw else { unreachable!() };
        Arc::new(File(Mutex::new(raw)))
    }

    fn raw(&self) -> Value {
        Value::Object(self.0.lock().unwrap().clone())
    }
}

/// The login Keychain, or one that is locked and answers nothing.
#[derive(Debug, Default)]
struct Kept {
    items: Mutex<HashMap<String, String>>,
    locked: bool,
}

impl Keychain for Kept {
    fn find(&self, item: &str) -> Result<Option<Secret>, KeychainProblem> {
        if self.locked {
            return Err(KeychainProblem);
        }
        Ok(self.items.lock().unwrap().get(item).map(|s| Secret::new(s.clone())))
    }

    fn put(&self, item: &str, secret: &Secret) -> Result<(), KeychainProblem> {
        if self.locked {
            return Err(KeychainProblem);
        }
        self.items.lock().unwrap().insert(item.to_owned(), secret.expose().to_owned());
        Ok(())
    }

    fn delete(&self, item: &str) -> Result<bool, KeychainProblem> {
        if self.locked {
            return Err(KeychainProblem);
        }
        Ok(self.items.lock().unwrap().remove(item).is_some())
    }
}

impl Kept {
    fn holds(&self) -> HashMap<String, String> {
        self.items.lock().unwrap().clone()
    }

    fn locked() -> Arc<Kept> {
        Arc::new(Kept { locked: true, ..Kept::default() })
    }
}

/// `op read`, printing the same for every reference; how many times it was asked.
#[derive(Debug, Default)]
struct Op(Mutex<usize>);

impl OnePassword for Op {
    fn read(&self, _reference: &str) -> Result<Secret, Fault> {
        *self.0.lock().unwrap() += 1;
        Ok(Secret::new("from-op".to_owned()))
    }
}

#[derive(Debug)]
struct At;

impl Clock for At {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000)
    }
}

fn directory(file: &Arc<File>, keychain: &Arc<Kept>) -> Directory {
    Directory::new(file.clone(), keychain.clone(), Arc::new(At))
}

fn unreachable(said: &str) -> Failure {
    Problem::new(Kind::Unreachable, said).into()
}

// -- the Claude API key --------------------------------------------------------------------------------------------

#[test]
fn the_key_is_kept_in_its_item_and_the_file_names_the_item_one_version_on() {
    let file = File::holding(json!({}));
    let keychain = Arc::new(Kept::default());
    let directory = directory(&file, &keychain);
    assert!(!directory.api_key_set().unwrap());
    directory.set_api_key(Secret::new("sk-ant-first".to_owned())).unwrap();
    assert_eq!(keychain.holds(), HashMap::from([(KEY_ITEM.to_owned(), "sk-ant-first".to_owned())]));
    // A file with no accounts is given an empty list first, as Python's `setdefault` does.
    assert_eq!(file.raw(), json!({"accounts": [], "anthropic_api_key": {"keychain": KEY_ITEM, "version": 1}}));
    assert!(directory.api_key_set().unwrap());
    directory.set_api_key(Secret::new("sk-ant-second".to_owned())).unwrap();
    assert_eq!(keychain.holds()[KEY_ITEM], "sk-ant-second");
    assert_eq!(file.raw()["anthropic_api_key"], json!({"keychain": KEY_ITEM, "version": 2}));
}

#[test]
fn a_key_kept_in_place_of_one_1password_held_lets_go_of_what_op_read() {
    let file = File::holding(json!({"accounts": [], "anthropic_api_key": {"op": OP_KEY}, "theme": "dark"}));
    let keychain = Arc::new(Kept::default());
    let op = Arc::new(Op::default());
    let reads = Arc::new(OpReads::new(op.clone()));
    let directory = directory(&file, &keychain).reading_op(reads.clone());
    reads.read(&json!(OP_KEY), false).unwrap();
    directory.set_api_key(Secret::new("sk-ant-kept".to_owned())).unwrap();
    // Named where it was, the rest of the file as it was, and the reference read afresh if it is named again.
    let raw = file.raw();
    assert_eq!(raw["anthropic_api_key"], json!({"keychain": KEY_ITEM, "version": 1}));
    assert_eq!(raw.as_object().unwrap().keys().collect::<Vec<_>>(), ["accounts", "anthropic_api_key", "theme"]);
    reads.read(&json!(OP_KEY), false).unwrap();
    assert_eq!(*op.0.lock().unwrap(), 2);
}

#[test]
fn clearing_the_key_takes_its_item_and_whatever_the_file_named_out() {
    let file = File::holding(json!({"accounts": [], "anthropic_api_key": {"keychain": KEY_ITEM, "version": 3}}));
    let keychain = Arc::new(Kept::default());
    keychain.items.lock().unwrap().insert(KEY_ITEM.to_owned(), "sk-ant-kept".to_owned());
    let directory = directory(&file, &keychain);
    directory.clear_api_key().unwrap();
    assert!(keychain.holds().is_empty());
    assert_eq!(file.raw(), json!({"accounts": []}));
    assert!(!directory.api_key_set().unwrap());
    // Nothing named, nothing kept: still done.
    directory.clear_api_key().unwrap();

    // A key the file names in another style goes too.
    let file = File::holding(json!({"accounts": [], "anthropic_api_key": {"env": "MY_KEY"}}));
    self::directory(&file, &keychain).clear_api_key().unwrap();
    assert_eq!(file.raw(), json!({"accounts": []}));
}

#[test]
fn a_key_the_keychain_cant_hold_is_refused_in_its_words_and_nothing_changes() {
    let file = File::holding(json!({"accounts": []}));
    let keychain = Arc::new(Kept::default());
    let directory = directory(&file, &keychain);
    for typed in ["", "sk-ant-caf\u{e9}", "two\nlines"] {
        let refused = directory.set_api_key(Secret::new(typed.to_owned())).unwrap_err();
        assert_eq!(refused, Problem::refused(UNPRINTABLE).into(), "{typed:?}");
    }
    assert!(keychain.holds().is_empty());
    assert_eq!(file.raw(), json!({"accounts": []}));
}

#[test]
fn a_keychain_that_wont_is_unreachable_and_the_file_is_left_as_it_was() {
    let named = json!({"accounts": [], "anthropic_api_key": {"keychain": KEY_ITEM, "version": 1}});
    let file = File::holding(named.clone());
    let directory = directory(&file, &Kept::locked());
    let kept = directory.set_api_key(Secret::new("sk-ant-new".to_owned())).unwrap_err();
    assert_eq!(kept, unreachable("The Keychain didn't keep the password. Is the login keychain locked?"));
    let cleared = directory.clear_api_key().unwrap_err();
    assert_eq!(cleared, unreachable("The Keychain didn't let go of the key. Is the login keychain locked?"));
    assert_eq!(file.raw(), named);
}

// -- the Proton Bridge's password ----------------------------------------------------------------------------------

/// Logins that always work.
#[derive(Debug)]
struct Probe;

impl LoginProbe for Probe {
    fn saved(&self, _account: &Account, _timeout: Duration) -> Result<(), LoginTrouble> {
        Ok(())
    }

    fn draft(&self, _draft: &Draft, _timeout: Duration) -> Result<(), LoginTrouble> {
        Ok(())
    }
}

#[derive(Debug, Default)]
struct Heard(Mutex<Vec<AccountChange>>);

impl AccountEvents for Heard {
    fn publish(&self, change: AccountChange) {
        self.0.lock().unwrap().push(change);
    }
}

impl Heard {
    fn taken(&self) -> Vec<AccountChange> {
        std::mem::take(&mut *self.0.lock().unwrap())
    }
}

fn proton(email: &str, credential: Value) -> Value {
    json!({"email": email, "type": "proton", "host": "127.0.0.1", "port": 1143, "credential": credential})
}

/// Two accounts on the Bridge, one of them disabled, and one that isn't.
fn bridged() -> Arc<File> {
    let mut disabled = proton("cy@example.test", json!({"proton_bridge": true}));
    disabled["disabled"] = json!(true);
    File::holding(json!({"accounts": [
        proton("ada@example.test", json!({"keychain": "account:ada@example.test"})),
        proton("bea@example.test", json!({"proton_bridge": true})),
        disabled,
    ]}))
}

fn connections(directory: &Arc<Directory>, heard: &Arc<Heard>) -> Connections {
    Connections::new(directory.clone(), Arc::new(Probe), heard.clone(), Arc::new(At))
}

fn password(directory: &Directory, email: &str) -> Result<String, Fault> {
    let account = directory.get(email).unwrap().unwrap();
    directory.password(&account).map(|secret| secret.expose().to_owned())
}

#[test]
fn the_bridges_password_is_kept_in_its_item_and_each_account_on_the_bridge_hears_it_changed() {
    let file = bridged();
    let before = file.raw();
    let keychain = Arc::new(Kept::default());
    let directory = Arc::new(directory(&file, &keychain));
    let heard = Arc::new(Heard::default());
    let connections = connections(&directory, &heard);
    assert!(!directory.bridge_password_set().unwrap());
    connections.set_bridge_password(Secret::new("bridge-first".to_owned())).unwrap();
    assert_eq!(keychain.holds(), HashMap::from([(ITEM.to_owned(), "bridge-first".to_owned())]));
    assert!(directory.bridge_password_set().unwrap());
    // Its login is let go of any hold, and IDLE logs in afresh; the disabled one waits until it is enabled.
    assert_eq!(heard.taken(), [AccountChange::Changed("bea@example.test".to_owned())]);
    // The file names no Bridge password.
    assert_eq!(file.raw(), before);
    connections.clear_bridge_password().unwrap();
    assert!(keychain.holds().is_empty() && !directory.bridge_password_set().unwrap());
    assert_eq!(heard.taken(), [AccountChange::Changed("bea@example.test".to_owned())]);
    assert_eq!(file.raw(), before);
}

#[test]
fn a_new_password_takes_the_place_of_the_one_a_login_found_and_one_taken_out_is_found_no_more() {
    let keychain = Arc::new(Kept::default());
    let directory = Arc::new(directory(&bridged(), &keychain));
    let connections = connections(&directory, &Arc::new(Heard::default()));
    connections.set_bridge_password(Secret::new("bridge-first".to_owned())).unwrap();
    assert_eq!(password(&directory, "bea@example.test").unwrap(), "bridge-first");
    connections.set_bridge_password(Secret::new("bridge-second".to_owned())).unwrap();
    assert_eq!(password(&directory, "bea@example.test").unwrap(), "bridge-second");
    connections.clear_bridge_password().unwrap();
    assert_eq!(password(&directory, "bea@example.test").unwrap_err(), Fault::new("NoCredential"));
}

#[test]
fn a_bridge_password_the_keychain_cant_hold_is_refused_in_its_words() {
    let keychain = Arc::new(Kept::default());
    let directory = Arc::new(directory(&bridged(), &keychain));
    let heard = Arc::new(Heard::default());
    let connections = connections(&directory, &heard);
    for typed in ["", "caf\u{e9}", "two\nlines"] {
        let refused = connections.set_bridge_password(Secret::new(typed.to_owned())).unwrap_err();
        assert_eq!(refused, Problem::refused(UNPRINTABLE).into(), "{typed:?}");
    }
    assert!(keychain.holds().is_empty() && heard.taken().is_empty());
}

#[test]
fn a_keychain_that_wont_keep_answer_or_let_go_of_the_bridges_password_is_unreachable() {
    let directory = Arc::new(directory(&bridged(), &Kept::locked()));
    let heard = Arc::new(Heard::default());
    let connections = connections(&directory, &heard);
    assert_eq!(
        directory.bridge_password_set().unwrap_err(),
        unreachable("The Keychain didn't answer. Is the login keychain locked?")
    );
    assert_eq!(
        connections.set_bridge_password(Secret::new("bridge".to_owned())).unwrap_err(),
        unreachable("The Keychain didn't keep the password. Is the login keychain locked?")
    );
    assert_eq!(
        connections.clear_bridge_password().unwrap_err(),
        unreachable("The Keychain didn't let go of the password. Is the login keychain locked?")
    );
    assert!(heard.taken().is_empty());
}
