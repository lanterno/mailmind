//! The accounts as the config file holds them: what the API may show of each, the password each logs in with, and a
//! loopback certificate pinned on first use.

#![allow(clippy::unwrap_used)]

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use mm_accounts::directory::Pin;
use mm_accounts::ports::ConfigFile;
use mm_accounts::{Directory, tls_pin};
use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Keychain, KeychainProblem, Moment, Secret};
use serde_json::{Map, Value, json};

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

#[derive(Debug, Default)]
struct Held(HashMap<String, String>);

impl Keychain for Held {
    fn find(&self, item: &str) -> Result<Option<Secret>, KeychainProblem> {
        Ok(self.0.get(item).map(|s| Secret::new(s.clone())))
    }
}

#[derive(Debug)]
struct At(Moment);

impl Clock for At {
    fn now(&self) -> Moment {
        self.0
    }
}

fn ada() -> Value {
    json!({"email": "ada@example.test", "type": "imap", "alias": " Ada ", "host": "127.0.0.1", "port": 1143,
           "starttls": true, "smtp_host": "127.0.0.1", "smtp_port": 1025, "smtp_starttls": true,
           "credential": {"keychain": "account:ada@example.test"}})
}

fn directory(accounts: Value) -> (Directory, Arc<File>) {
    let Value::Object(raw) = json!({"accounts": accounts, "signature_default": "  Ada\u{200b}\n"}) else {
        unreachable!()
    };
    let file = Arc::new(File(Mutex::new(raw)));
    let keychain = Held(HashMap::from([("account:ada@example.test".to_owned(), "app-password-0".to_owned())]));
    let clock = At(Moment::from_micros(1_793_620_800_000_000));
    (Directory::new(file.clone(), Arc::new(keychain), Arc::new(clock)), file)
}

#[test]
fn an_account_is_listed_as_the_api_may_show_it() {
    let (directory, _) = directory(json!([ada(), {"email": "bea@example.test", "type": "proton", "disabled": 1}]));
    let listed = directory.listed().unwrap();
    let ada = &listed["accounts"][0];
    let keys: Vec<&str> = ada.as_object().unwrap().keys().map(String::as_str).collect();
    assert_eq!(
        keys,
        [
            "email",
            "type",
            "alias",
            "host",
            "port",
            "starttls",
            "trash",
            "smtp_host",
            "smtp_port",
            "smtp_starttls",
            "disabled",
            "defaults_applied",
            "credential",
            "signature"
        ]
    );
    assert_eq!(ada["alias"], "Ada");
    assert_eq!(ada["trash"], "Trash");
    assert_eq!(ada["defaults_applied"]["trash"], true);
    assert_eq!(ada["defaults_applied"]["port"], false);
    assert_eq!(ada["credential"], json!({"style": "keychain", "set": true, "item": "account:ada@example.test"}));
    let bea = &listed["accounts"][1];
    assert_eq!((&bea["host"], &bea["port"], &bea["disabled"]), (&json!("127.0.0.1"), &json!(1143), &json!(true)));
    assert_eq!(listed["signature_default"], "Ada");
    assert_eq!(listed["generated"], "2026-11-02T12:00:00+00:00");
    assert_eq!(listed["ok"], true);
}

#[test]
fn a_disabled_account_is_none_the_app_may_use() {
    let (directory, _) = directory(json!([ada(), {"email": "bea@example.test", "type": "proton", "disabled": true}]));
    let emails: Vec<String> = directory.accounts().unwrap().into_iter().map(|a| a.email).collect();
    assert_eq!(emails, ["ada@example.test"]);
    assert!(directory.get("bea@example.test").unwrap().is_none());
    assert!(directory.get("ADA@example.test").unwrap().is_none(), "an account is got by its address exactly");
    assert_eq!(directory.get("ada@example.test").unwrap().unwrap().imap_server(), Some(("127.0.0.1".into(), 1143)));
}

#[test]
fn an_accounts_trash_is_its_entrys_or_its_types() {
    let (directory, _) = directory(json!([
        ada(),
        {"email": "bea@example.test", "type": "proton", "trash": "Bin"},
        {"email": "cy@example.test", "type": "gmail", "trash": ""},
    ]));
    let trash = |email: &str| directory.get(email).unwrap().unwrap().trash;
    assert_eq!(trash("ada@example.test"), "Trash");
    assert_eq!(trash("bea@example.test"), "Bin");
    assert_eq!(trash("cy@example.test"), "[Gmail]/Trash", "an empty one is the type's");
}

#[test]
fn the_password_comes_from_the_keychain_item_the_credential_names() {
    let (directory, _) = directory(json!([ada()]));
    let account = directory.get("ada@example.test").unwrap().unwrap();
    assert_eq!(directory.password(&account).unwrap().expose(), "app-password-0");

    let mut env = ada();
    env["credential"] = json!({"env": "ADA_PASSWORD"});
    let (directory, _) = self::directory(json!([env]));
    let account = directory.get("ada@example.test").unwrap().unwrap();
    assert_eq!(directory.password(&account).unwrap_err(), Fault::new("CredentialStyleNotOnTheRustServerYet"));

    let mut missing = ada();
    missing["credential"] = json!({"keychain": "account:nobody@example.test"});
    let (directory, _) = self::directory(json!([missing]));
    let account = directory.get("ada@example.test").unwrap().unwrap();
    assert_eq!(directory.password(&account).unwrap_err(), Fault::new("NoCredential"));
}

#[test]
fn a_loopback_certificate_is_pinned_on_first_use_and_held_to_after() {
    let (directory, file) = directory(json!([ada()]));
    let endpoint = tls_pin::endpoint(" 127.0.0.1 ", 1143);
    assert_eq!(endpoint, "127.0.0.1:1143");
    let first = tls_pin::fingerprint(b"the first certificate");
    assert!(first.starts_with("sha256:") && first.len() == 7 + 64);

    assert_eq!(directory.tls_pin("ada@example.test", &endpoint, &first).unwrap(), Pin::Kept);
    assert_eq!(file.read().unwrap()["accounts"][0]["tls_pins"], json!({"127.0.0.1:1143": first}));
    assert_eq!(directory.tls_pin("ADA@example.test ", &endpoint, &first).unwrap(), Pin::Kept);
    let other = tls_pin::fingerprint(b"another certificate");
    assert_eq!(directory.tls_pin("ada@example.test", &endpoint, &other).unwrap(), Pin::Other);
    assert_eq!(directory.tls_pin("nobody@example.test", &endpoint, &other).unwrap(), Pin::NoAccount);
    assert!(
        tls_pin::is_loopback("LOCALHOST") && tls_pin::is_loopback("::1") && !tls_pin::is_loopback("imap.example.test")
    );
}

#[test]
fn an_account_s_alias_is_what_its_entry_names_stripped_and_none_when_blank() {
    let alias = |given: Value| {
        let mut entry = ada();
        entry["alias"] = given;
        mm_accounts::Account::from_entry(&entry).unwrap().alias
    };
    assert_eq!(alias(json!(" Ada ")), Some("Ada".to_owned()));
    for blank in [json!("  "), json!(""), Value::Null, json!(7)] {
        assert_eq!(alias(blank), None);
    }
    let mut unnamed = ada();
    unnamed.as_object_mut().unwrap().remove("alias");
    assert_eq!(mm_accounts::Account::from_entry(&unnamed).unwrap().alias, None);
}
