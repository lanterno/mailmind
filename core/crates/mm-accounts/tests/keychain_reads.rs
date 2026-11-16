//! A Keychain item read once a process for each version the config names, as the Python app reads one
//! (`secrets._keychain_read`), and forgotten as MailMind keeps the item again or takes it out, or the credential that
//! named it is replaced (`keep`, `discard`, `evict`).

#![allow(clippy::unwrap_used)]

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use mm_accounts::Directory;
use mm_accounts::keychain_reads::KeychainReads;
use mm_accounts::ports::ConfigFile;
use mm_kernel::ai_provider::AiProvider::Anthropic as Claude;
use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Keychain, KeychainProblem, Moment, Secret};
use serde_json::{Map, Value, json};

const ITEM: &str = "anthropic-api-key";
const OTHER: &str = "openai-api-key";

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

/// The login Keychain: what it keeps, how often each item was looked for, and whether it can be asked.
#[derive(Debug, Default)]
struct Kept {
    items: Mutex<HashMap<String, String>>,
    asked: Mutex<HashMap<String, usize>>,
    locked: Mutex<bool>,
}

impl Keychain for Kept {
    fn find(&self, item: &str) -> Result<Option<Secret>, KeychainProblem> {
        *self.asked.lock().unwrap().entry(item.to_owned()).or_default() += 1;
        if *self.locked.lock().unwrap() {
            return Err(KeychainProblem);
        }
        Ok(self.items.lock().unwrap().get(item).map(|s| Secret::new(s.clone())))
    }

    fn put(&self, item: &str, secret: &Secret) -> Result<(), KeychainProblem> {
        self.keep(item, secret.expose());
        Ok(())
    }

    fn delete(&self, item: &str) -> Result<bool, KeychainProblem> {
        Ok(self.items.lock().unwrap().remove(item).is_some())
    }
}

impl Kept {
    /// Keep `secret` as `item`, as Keychain Access or another process may.
    fn keep(&self, item: &str, secret: &str) {
        self.items.lock().unwrap().insert(item.to_owned(), secret.to_owned());
    }

    fn asked(&self, item: &str) -> usize {
        self.asked.lock().unwrap().get(item).copied().unwrap_or(0)
    }
}

#[derive(Debug)]
struct At;

impl Clock for At {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000)
    }
}

fn read(reads: &KeychainReads, item: &str, version: &Value) -> Option<String> {
    reads.read(item, version).unwrap().map(|secret| secret.expose().to_owned())
}

#[test]
fn an_item_is_read_once_for_each_version_and_kept_only_once_it_is_found() {
    let keychain = Arc::new(Kept::default());
    let reads = KeychainReads::new(keychain.clone());
    assert_eq!((read(&reads, ITEM, &json!(1)), read(&reads, ITEM, &json!(1))), (None, None));
    assert_eq!(keychain.asked(ITEM), 2, "one not found is looked for again");
    keychain.keep(ITEM, "sk-ant-fictional-one");
    for _ in 0..3 {
        assert_eq!(read(&reads, ITEM, &json!(1)).as_deref(), Some("sk-ant-fictional-one"));
    }
    assert_eq!(keychain.asked(ITEM), 3, "found, it is read no more");

    // Kept again outside MailMind: the version the file names says when it is read again.
    keychain.keep(ITEM, "sk-ant-fictional-two");
    assert_eq!(read(&reads, ITEM, &json!(1)).as_deref(), Some("sk-ant-fictional-one"));
    assert_eq!(read(&reads, ITEM, &json!(2)).as_deref(), Some("sk-ant-fictional-two"));
    // A version written otherwise, or none, is another version.
    assert_eq!(read(&reads, ITEM, &json!("2")).as_deref(), Some("sk-ant-fictional-two"));
    assert_eq!(read(&reads, ITEM, &Value::Null).as_deref(), Some("sk-ant-fictional-two"));
    assert_eq!(keychain.asked(ITEM), 6);

    // A Keychain that can't be asked keeps nothing, and is asked again.
    *keychain.locked.lock().unwrap() = true;
    assert!(reads.read(OTHER, &json!(1)).is_err());
    *keychain.locked.lock().unwrap() = false;
    keychain.keep(OTHER, "sk-fictional");
    assert_eq!(read(&reads, OTHER, &json!(1)).as_deref(), Some("sk-fictional"));
    assert_eq!(keychain.asked(OTHER), 2);

    // Forgotten, every version of the item is read again, and no other item.
    reads.forget(ITEM);
    for version in [json!(1), json!(2)] {
        assert_eq!(read(&reads, ITEM, &version).as_deref(), Some("sk-ant-fictional-two"));
    }
    assert_eq!(read(&reads, OTHER, &json!(1)).as_deref(), Some("sk-fictional"));
    assert_eq!((keychain.asked(ITEM), keychain.asked(OTHER)), (8, 2));
}

#[test]
fn a_key_kept_again_or_taken_out_through_the_directory_is_read_afresh() {
    let (file, keychain) = (Arc::new(File::default()), Arc::new(Kept::default()));
    let reads = Arc::new(KeychainReads::new(keychain.clone()));
    let directory = Directory::new(file.clone(), keychain.clone(), Arc::new(At)).reading_keychain(reads.clone());
    directory.set_api_key(Claude, Secret::new("sk-ant-fictional-one".into())).unwrap();
    assert_eq!(file.read().unwrap()["anthropic_api_key"], json!({"keychain": ITEM, "version": 1}));
    assert_eq!(read(&reads, ITEM, &json!(1)).as_deref(), Some("sk-ant-fictional-one"));

    // Kept again: read afresh, even under a version read before (a file written back as it was).
    directory.set_api_key(Claude, Secret::new("sk-ant-fictional-two".into())).unwrap();
    assert_eq!(read(&reads, ITEM, &json!(1)).as_deref(), Some("sk-ant-fictional-two"));
    // Taken out: there is none.
    directory.clear_api_key(Claude).unwrap();
    assert_eq!(read(&reads, ITEM, &json!(1)), None);

    // A credential replaced lets go of what was read of the item it named.
    file.write(json!({"anthropic_api_key": {"keychain": "claude-by-hand", "version": 1}}).as_object().unwrap())
        .unwrap();
    keychain.keep("claude-by-hand", "sk-ant-fictional-old");
    assert_eq!(read(&reads, "claude-by-hand", &json!(1)).as_deref(), Some("sk-ant-fictional-old"));
    directory.set_api_key(Claude, Secret::new("sk-ant-fictional-three".into())).unwrap();
    keychain.keep("claude-by-hand", "sk-ant-fictional-new");
    assert_eq!(read(&reads, "claude-by-hand", &json!(1)).as_deref(), Some("sk-ant-fictional-new"));

    // The Bridge's password alike.
    keychain.keep(mm_accounts::bridge::ITEM, "bridge-one");
    assert_eq!(read(&reads, mm_accounts::bridge::ITEM, &Value::Null).as_deref(), Some("bridge-one"));
    directory.set_bridge_password(Secret::new("bridge-two".into())).unwrap();
    assert_eq!(read(&reads, mm_accounts::bridge::ITEM, &Value::Null).as_deref(), Some("bridge-two"));
    directory.clear_bridge_password().unwrap();
    assert_eq!(read(&reads, mm_accounts::bridge::ITEM, &Value::Null), None);
}
