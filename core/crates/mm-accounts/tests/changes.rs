//! What Settings changes in the config file: an account added, edited and removed, its pins forgotten, and a typed
//! password kept in the Keychain rather than the file (`Directory.add_account` and the rest).

#![allow(clippy::unwrap_used)]

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use mm_accounts::ports::ConfigFile;
use mm_accounts::{Directory, account};
use mm_kernel::errors::{Failure, Fault, Kind, Problem};
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

impl File {
    fn accounts(&self) -> Value {
        self.0.lock().unwrap()["accounts"].clone()
    }
}

/// The login Keychain, or one that is locked and keeps nothing.
#[derive(Debug, Default)]
struct Kept {
    items: Mutex<HashMap<String, String>>,
    locked: bool,
}

impl Keychain for Kept {
    fn find(&self, item: &str) -> Result<Option<Secret>, KeychainProblem> {
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
}

#[derive(Debug)]
struct At(Moment);

impl Clock for At {
    fn now(&self) -> Moment {
        self.0
    }
}

fn object(value: Value) -> Map<String, Value> {
    match value {
        Value::Object(map) => map,
        _ => unreachable!(),
    }
}

fn ada() -> Value {
    json!({"email": "ada@example.test", "type": "imap", "host": "127.0.0.1", "port": 1143, "starttls": true,
           "credential": {"keychain": "account:ada@example.test"},
           "tls_pins": {"127.0.0.1:1143": "sha256:aa"}})
}

fn directory(accounts: Value, keychain: Kept) -> (Directory, Arc<File>, Arc<Kept>) {
    let file = Arc::new(File(Mutex::new(object(json!({"accounts": accounts})))));
    let keychain = Arc::new(keychain);
    let clock = At(Moment::from_micros(1_793_620_800_000_000));
    (Directory::new(file.clone(), keychain.clone(), Arc::new(clock)), file, keychain)
}

const DEE: &str = "dee@example.test";

fn dee(credential: Value) -> Map<String, Value> {
    object(json!({"email": DEE, "type": "imap", "host": "imap.example.test", "port": 993, "alias": " Dee ",
                  "credential": credential}))
}

fn typed(password: &str) -> Value {
    json!({"style": "keychain", "value": password})
}

fn refused(said: &str) -> Failure {
    Problem::refused(said).into()
}

#[test]
fn an_account_added_keeps_its_typed_password_in_the_keychain_and_names_the_item_in_the_file() {
    let (directory, file, keychain) = directory(json!([ada()]), Kept::default());
    let row = directory.add_account(&dee(typed("dee-password"))).unwrap();
    assert_eq!(row["email"], DEE);
    assert_eq!(row["alias"], "Dee");
    assert_eq!(row["credential"], json!({"style": "keychain", "set": true, "item": "account:dee@example.test"}));
    assert_eq!(
        file.accounts()[1],
        json!({"email": DEE, "type": "imap", "alias": "Dee", "host": "imap.example.test", "port": 993,
               "credential": {"keychain": "account:dee@example.test", "version": 1}})
    );
    assert_eq!(keychain.holds()["account:dee@example.test"], "dee-password");
    assert!(!serde_json::to_string(&file.accounts()).unwrap().contains("dee-password"));
}

#[test]
fn an_account_is_added_once_whatever_the_case_or_spaces_of_its_address() {
    let (directory, file, _) = directory(json!([ada()]), Kept::default());
    let mut again = dee(typed("x"));
    again.insert("email".into(), json!("  ADA@example.test "));
    assert_eq!(directory.add_account(&again).unwrap_err(), refused("ADA@example.test already exists"));
    assert_eq!(file.accounts(), json!([ada()]));
}

#[test]
fn what_an_account_cant_be_is_refused_and_writes_nothing() {
    let cases: Vec<(Value, &str)> = vec![
        (json!({"email": DEE, "type": "imap", "host": "h.example.test"}), "a credential is required"),
        (
            json!({"email": DEE, "type": "imap", "credential": typed("x")}),
            "a host is required for a generic IMAP account",
        ),
        (
            json!({"email": "dee", "type": "imap", "host": "h.example.test", "credential": typed("x")}),
            "a valid email is required",
        ),
        (
            json!({"email": DEE, "type": "pop3", "host": "h.example.test", "credential": typed("x")}),
            "type must be gmail, imap, or proton",
        ),
        (
            json!({"email": DEE, "type": ["imap"], "host": "h.example.test", "credential": typed("x")}),
            "type must be gmail, imap, or proton",
        ),
        (
            json!({"email": DEE, "type": null, "host": "h.example.test", "credential": typed("x")}),
            "type must be gmail, imap, or proton",
        ),
        (
            json!({"email": DEE, "host": "h.example.test", "port": 70000, "credential": typed("x")}),
            "port must be 1–65535",
        ),
        (
            json!({"email": DEE, "host": "h.example.test", "port": "nine", "credential": typed("x")}),
            "port must be 1–65535",
        ),
        (json!({"email": DEE, "host": "h example.test", "credential": typed("x")}), "host must be a host name"),
        (json!({"email": DEE, "host": 7, "credential": typed("x")}), "host must be a host name"),
        (
            json!({"email": DEE, "host": "h.example.test", "smtp_host": "s\n", "credential": typed("x")}),
            "SMTP host must be a host name",
        ),
        (
            json!({"email": DEE, "host": "h.example.test", "smtp_port": 0, "credential": typed("x")}),
            "SMTP port must be 1–65535",
        ),
        (json!({"email": DEE, "host": "h.example.test", "credential": {"style": "keychain"}}), "password required"),
        (
            json!({"email": DEE, "host": "h.example.test", "credential": {"style": "op", "ref": " "}}),
            "1Password reference required",
        ),
        (
            json!({"email": DEE, "host": "h.example.test", "credential": {"style": "env"}}),
            "environment variable name required",
        ),
        (json!({"email": DEE, "host": "h.example.test", "credential": "hunter2"}), "a credential names its style"),
        (
            json!({"email": DEE, "host": "h.example.test", "credential": typed("caf\u{e9}")}),
            "A secret MailMind keeps in the Keychain is printable ASCII, and not empty: letters, digits, punctuation and spaces.",
        ),
        (
            json!({"email": "dee example@example.test", "host": "h.example.test", "credential": typed("x")}),
            "A Keychain item MailMind keeps is named in letters, digits and . _ % + @ : = - alone.",
        ),
    ];
    for (asked, said) in cases {
        let (directory, file, keychain) = directory(json!([]), Kept::default());
        assert_eq!(directory.add_account(&object(asked.clone())).unwrap_err(), refused(said), "{asked}");
        assert_eq!(file.accounts(), json!([]), "{asked}");
        assert!(keychain.holds().is_empty(), "{asked}");
    }
}

#[test]
fn a_refusal_holds_nothing_typed() {
    let (directory, _, _) = directory(json!([]), Kept::default());
    for asked in [dee(typed("caf\u{e9}-hunter2")), dee(json!({"style": "op", "ref": "", "value": "caf\u{e9}-hunter2"}))]
    {
        let Failure::Problem(problem) = directory.add_account(&asked).unwrap_err() else { panic!("a fault") };
        assert!(!problem.said.contains("hunter2"), "{}", problem.said);
    }
}

#[test]
fn a_locked_keychain_leaves_the_file_as_it_was() {
    let (directory, file, _) = directory(json!([ada()]), Kept { locked: true, ..Kept::default() });
    let Failure::Problem(problem) = directory.add_account(&dee(typed("dee-password"))).unwrap_err() else {
        panic!("a fault")
    };
    assert_eq!(
        (problem.kind, problem.said.as_str()),
        (Kind::Unreachable, "The Keychain didn't keep the password. Is the login keychain locked?")
    );
    assert_eq!(file.accounts(), json!([ada()]));
    let Failure::Problem(problem) = directory.remove_account("ada@example.test").unwrap_err() else {
        panic!("a fault")
    };
    assert_eq!(problem.kind, Kind::Unreachable);
    assert_eq!(file.accounts(), json!([ada()]));
}

#[test]
fn a_stored_secret_is_named_in_the_file_and_nothing_is_kept() {
    let (directory, file, keychain) = directory(json!([]), Kept::default());
    directory.add_account(&dee(json!({"style": "op", "ref": "op://Private/Dee/password"}))).unwrap();
    assert_eq!(file.accounts()[0]["credential"], json!({"op": "op://Private/Dee/password"}));
    let mut edit = Map::new();
    edit.insert("credential".into(), json!({"style": "env", "var": " DEE_PASSWORD "}));
    directory.update_account(DEE, &edit).unwrap();
    assert_eq!(file.accounts()[0]["credential"], json!({"env": "DEE_PASSWORD"}));
    assert!(keychain.holds().is_empty());
}

#[test]
fn an_edit_changes_what_it_names_and_leaves_the_rest() {
    let (directory, file, keychain) = directory(json!([ada()]), Kept::default());
    let asked =
        object(json!({"email": "ada@example.test", "alias": "Ada L.", "signature": "Ada\n-- sent", "port": "993",
                              "starttls": false, "host": "", "smtp_host": null, "disabled": "yes"}));
    let row = directory.update_account("ada@example.test", &asked).unwrap().unwrap();
    assert_eq!(
        (row["alias"].clone(), row["port"].clone(), row["starttls"].clone()),
        (json!("Ada L."), json!(993), json!(false))
    );
    let mut kept = object(ada());
    for (key, value) in [
        ("port", json!(993)),
        ("starttls", json!(false)),
        ("alias", json!("Ada L.")),
        ("signature", json!("Ada\n-- sent")),
    ] {
        kept.insert(key.into(), value);
    }
    assert_eq!(file.accounts()[0], Value::Object(kept));
    assert!(keychain.holds().is_empty());
}

#[test]
fn a_blank_alias_or_signature_unsets_it_and_the_file_keeps_its_order() {
    let mut entry = object(ada());
    entry.insert("alias".into(), json!("Ada"));
    entry.insert("signature".into(), json!("Ada"));
    let (directory, file, _) = directory(json!([entry]), Kept::default());
    directory.update_account("ada@example.test", &object(json!({"alias": "  ", "signature": null}))).unwrap();
    let keys: Vec<String> = file.accounts()[0].as_object().unwrap().keys().cloned().collect();
    assert_eq!(keys, ["email", "type", "host", "port", "starttls", "credential", "tls_pins"]);
}

#[test]
fn what_the_file_held_isnt_refused_when_an_edit_leaves_it_be() {
    let mut entry = object(ada());
    entry.insert("smtp_host".into(), json!("smtp example.test"));
    let (directory, _, _) = directory(json!([entry]), Kept::default());
    let row = directory.update_account("ada@example.test", &object(json!({"alias": "Ada"}))).unwrap().unwrap();
    assert_eq!(row["alias"], "Ada");
    let refused_now = directory.update_account("ada@example.test", &object(json!({"smtp_host": "smtp\texample.test"})));
    assert_eq!(refused_now.unwrap_err(), refused("SMTP host must be a host name"));
}

#[test]
fn a_new_password_is_a_new_version_of_the_same_item() {
    let (directory, file, keychain) = directory(json!([ada()]), Kept::default());
    for (password, version) in [("first-password", 1), ("second-password", 2)] {
        let asked = object(json!({"credential": typed(password)}));
        directory.update_account("ada@example.test", &asked).unwrap();
        assert_eq!(
            file.accounts()[0]["credential"],
            json!({"keychain": "account:ada@example.test", "version": version})
        );
        assert_eq!(keychain.holds()["account:ada@example.test"], password);
    }
}

#[test]
fn a_credential_with_no_style_keeps_the_one_there_is() {
    let (directory, file, _) = directory(json!([ada()]), Kept::default());
    for credential in [json!(null), json!({}), json!({"style": "plain", "value": "x"})] {
        directory.update_account("ada@example.test", &object(json!({"credential": credential}))).unwrap();
        assert_eq!(file.accounts()[0]["credential"], json!({"keychain": "account:ada@example.test"}));
    }
}

#[test]
fn moving_a_password_out_of_the_keychain_lets_its_own_item_go() {
    let keychain = Kept::default();
    keychain.items.lock().unwrap().insert("account:ada@example.test".into(), "app-password".into());
    let (directory, file, keychain) = directory(json!([ada()]), keychain);
    let asked = object(json!({"credential": {"style": "op", "ref": ["op://Private/Ada/password"]}}));
    directory.update_account("ada@example.test", &asked).unwrap();
    assert_eq!(file.accounts()[0]["credential"], json!({"op": ["op://Private/Ada/password"]}));
    assert!(keychain.holds().is_empty());
}

#[test]
fn an_account_no_file_holds_is_none_to_edit_remove_or_trust() {
    let (directory, file, _) = directory(json!([ada()]), Kept::default());
    assert_eq!(directory.update_account("zed@example.test", &object(json!({"alias": "Z"}))).unwrap(), None);
    assert_eq!(directory.remove_account("zed@example.test").unwrap(), None);
    assert_eq!(directory.forget_tls_pins("zed@example.test").unwrap(), None);
    assert_eq!(file.accounts(), json!([ada()]));
}

#[test]
fn removing_an_account_takes_its_entry_and_its_own_item_out() {
    let keychain = Kept::default();
    keychain.items.lock().unwrap().insert("account:ada@example.test".into(), "app-password".into());
    keychain.items.lock().unwrap().insert("account:bea@example.test".into(), "bea-password".into());
    let bea = json!({"email": "bea@example.test", "type": "proton"});
    let (directory, file, keychain) = directory(json!([ada(), bea]), keychain);
    assert_eq!(directory.remove_account(" Ada@Example.test").unwrap().as_deref(), Some("ada@example.test"));
    assert_eq!(file.accounts(), json!([bea]));
    assert_eq!(keychain.holds().into_keys().collect::<Vec<_>>(), ["account:bea@example.test"]);
}

#[test]
fn trusting_an_account_again_forgets_its_pins() {
    let (directory, file, _) = directory(json!([ada()]), Kept::default());
    assert_eq!(directory.forget_tls_pins("ada@example.test").unwrap().as_deref(), Some("ada@example.test"));
    assert!(!file.accounts()[0].as_object().unwrap().contains_key("tls_pins"));
    assert_eq!(directory.forget_tls_pins("ada@example.test").unwrap().as_deref(), Some("ada@example.test"));
}

#[test]
fn a_port_reads_as_a_number_and_a_flag_only_as_true_or_false() {
    let mut entry = Map::new();
    account::apply_fields(
        &mut entry,
        &object(json!({"port": " 143 ", "smtp_port": 587.0, "starttls": 1, "disabled": true})),
    )
    .unwrap();
    assert_eq!(Value::Object(entry), json!({"port": 143, "smtp_port": 587, "disabled": true}));
}
