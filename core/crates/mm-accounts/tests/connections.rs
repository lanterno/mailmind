//! What Settings asks of an account's connection, through the LoginProbe port and no server: a saved account's
//! credential goes only to its stored endpoint, whatever the request names, a draft tries only what was typed, and a
//! login that didn't work says so in words of its own (`Connections`, `throwaway_account`, `conn_error.quiet`).

#![allow(clippy::unwrap_used)]

use std::sync::{Arc, Mutex};
use std::time::Duration;

use mm_accounts::connections::{Connections, PROBE_TIMEOUT, Trusted, quiet};
use mm_accounts::draft::{Draft, NotTried, throwaway_account};
use mm_accounts::ports::{AccountChange, AccountEvents, ConfigFile, LoginProbe, LoginTrouble};
use mm_accounts::{Account, Directory};
use mm_kernel::errors::{Failure, Fault, Problem};
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

/// A Keychain that keeps whatever it is given.
#[derive(Debug, Default)]
struct Kept;

impl Keychain for Kept {
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

/// A clock a test login moves on by 42 ms.
#[derive(Debug, Default)]
struct Ticking(Mutex<i64>);

impl Clock for Ticking {
    fn now(&self) -> Moment {
        let mut at = self.0.lock().unwrap();
        *at += 42_000;
        Moment::from_micros(1_793_620_800_000_000 + *at)
    }
}

/// What a probe was asked: which login, where, with what, and for how long.
#[derive(Clone, Debug, PartialEq, Eq)]
enum Asked {
    Saved { email: String, host: Value, port: Value },
    Draft { email: Option<String>, host: Option<String>, port: u16, starttls: bool, password: String },
}

#[derive(Debug, Default)]
struct Probe {
    asked: Mutex<Vec<(Asked, Duration)>>,
    trouble: Option<&'static str>,
}

impl LoginProbe for Probe {
    fn saved(&self, account: &Account, timeout: Duration) -> Result<(), LoginTrouble> {
        let asked =
            Asked::Saved { email: account.email.clone(), host: account.host.clone(), port: account.port.clone() };
        self.asked.lock().unwrap().push((asked, timeout));
        self.trouble.map_or(Ok(()), |class| Err(LoginTrouble(class)))
    }

    fn draft(&self, draft: &Draft, timeout: Duration) -> Result<(), LoginTrouble> {
        let asked = Asked::Draft {
            email: draft.email.clone(),
            host: draft.host.clone(),
            port: draft.port,
            starttls: draft.starttls,
            password: draft.password.expose().to_owned(),
        };
        self.asked.lock().unwrap().push((asked, timeout));
        self.trouble.map_or(Ok(()), |class| Err(LoginTrouble(class)))
    }
}

#[derive(Debug, Default)]
struct Heard(Mutex<Vec<AccountChange>>);

impl AccountEvents for Heard {
    fn publish(&self, change: AccountChange) {
        self.0.lock().unwrap().push(change);
    }
}

const ADA: &str = "ada@example.test";

fn object(value: Value) -> Map<String, Value> {
    match value {
        Value::Object(map) => map,
        _ => unreachable!(),
    }
}

struct World {
    connections: Connections,
    probe: Arc<Probe>,
    heard: Arc<Heard>,
    file: Arc<File>,
}

fn world(trouble: Option<&'static str>) -> World {
    let accounts = json!([
        {"email": ADA, "type": "imap", "host": "imap.example.test", "port": 993,
         "credential": {"op": "op://Private/Ada/password"}, "tls_pins": {"127.0.0.1:1143": "sha256:aa"}},
        {"email": "bea@example.test", "type": "proton", "disabled": true, "credential": {"proton_bridge": true},
         "tls_pins": {"127.0.0.1:1143": "sha256:bb"}},
    ]);
    let file = Arc::new(File(Mutex::new(object(json!({"accounts": accounts})))));
    let clock: Arc<dyn Clock> = Arc::new(Ticking::default());
    let directory = Arc::new(Directory::new(file.clone(), Arc::new(Kept), clock.clone()));
    let probe = Arc::new(Probe { trouble, ..Probe::default() });
    let heard = Arc::new(Heard::default());
    let connections = Connections::new(directory, probe.clone(), heard.clone(), clock);
    World { connections, probe, heard, file }
}

fn typed(password: &str) -> Value {
    json!({"style": "keychain", "value": password})
}

#[test]
fn a_saved_account_goes_to_its_stored_endpoint_whatever_the_request_names() {
    let w = world(None);
    let asked = object(json!({"email": ADA, "host": "evil.example.test", "port": 25, "credential": typed("typed")}));
    let answer = w.connections.test(&asked).unwrap();
    assert_eq!(
        *w.probe.asked.lock().unwrap(),
        [(Asked::Saved { email: ADA.into(), host: json!("imap.example.test"), port: json!(993) }, PROBE_TIMEOUT)]
    );
    assert_eq!(answer, json!({"ok": true, "connect": true, "email": ADA, "detail": "login ok", "ms": 42}));
}

#[test]
fn a_draft_goes_as_a_draft_with_what_was_typed() {
    let w = world(None);
    let draft =
        json!({"email": "dee@example.test", "type": "imap", "host": "imap.example.test", "credential": typed("typed")});
    let answer = w.connections.test(&object(json!({"draft": draft}))).unwrap();
    let draft = Asked::Draft {
        email: Some("dee@example.test".into()),
        host: Some("imap.example.test".into()),
        port: 993,
        starttls: false,
        password: "typed".into(),
    };
    assert_eq!(*w.probe.asked.lock().unwrap(), [(draft, PROBE_TIMEOUT)]);
    assert_eq!((answer["connect"].clone(), answer["email"].clone()), (json!(true), json!("dee@example.test")));
}

#[test]
fn a_draft_the_domain_refuses_reaches_no_server() {
    let w = world(None);
    let draft = json!({"email": "dee@example.test", "host": "imap.example.test", "credential": {"style": "env", "var": "HOME"}});
    let refused = w.connections.test(&object(json!({"draft": draft}))).unwrap_err();
    let save_first = "save the account to test a stored secret; before then only a typed password is tried";
    assert_eq!(refused, Failure::Problem(Problem::refused(save_first)));
    assert_eq!(w.connections.test(&object(json!({"draft": "dee"}))).unwrap_err(), Problem::bad_request().into());
    assert!(w.probe.asked.lock().unwrap().is_empty());
}

#[test]
fn a_draft_whose_port_no_connection_could_reach_says_so() {
    for port in [json!("nine"), json!(70000), json!(-1), json!(18_446_744_073_709_551_615_u64), json!([993])] {
        let w = world(None);
        let draft =
            json!({"email": "dee@example.test", "host": "h.example.test", "port": port, "credential": typed("t")});
        let answer = w.connections.test(&object(json!({"draft": draft}))).unwrap();
        let said = json!({"ok": true, "connect": false, "email": "dee@example.test", "error": "port must be 1–65535"});
        assert_eq!(answer, said, "{port}");
        assert!(w.probe.asked.lock().unwrap().is_empty());
    }
}

#[test]
fn an_account_no_one_saved_is_not_found() {
    let w = world(None);
    for asked in [json!({"email": "zed@example.test"}), json!({}), json!({"email": null}), json!({"draft": {}})] {
        let not_found = w.connections.test(&object(asked)).unwrap_err();
        assert_eq!(not_found, Problem::not_found("account not found").into());
    }
    // A disabled account isn't one a connection may use.
    let not_found = w.connections.test(&object(json!({"email": "bea@example.test"}))).unwrap_err();
    assert_eq!(not_found, Problem::not_found("account not found").into());
}

#[test]
fn a_failure_says_what_went_wrong_in_words_of_its_own() {
    for (class, says) in [
        ("ConnectionRefused", "connection refused"),
        ("Timeout", "timeout"),
        ("LoginRefused", "AUTHENTICATIONFAILED"),
        ("TLSPinMismatch", "certificate changed"),
        ("NameNotResolved", "host not found"),
        ("CertificateNotVerified", "SSLCertVerificationError"),
        ("ConnectionLost", "ConnectionLost"),
    ] {
        let w = world(Some(class));
        let answer = w.connections.test(&object(json!({"email": ADA}))).unwrap();
        assert_eq!(answer, json!({"ok": true, "connect": false, "email": ADA, "error": says}), "{class}");
        assert_eq!(quiet(LoginTrouble(class)), says);
    }
}

#[test]
fn a_failed_draft_is_named_by_the_address_asked_first_and_then_the_drafts() {
    let w = world(Some("LoginRefused"));
    let draft = json!({"email": "dee@example.test", "host": "h.example.test", "credential": typed("t")});
    let answer = w.connections.test(&object(json!({"email": "", "draft": draft.clone()}))).unwrap();
    assert_eq!(answer["email"], "dee@example.test");
    let answer = w.connections.test(&object(json!({"email": "eve@example.test", "draft": draft}))).unwrap();
    assert_eq!(answer["email"], "eve@example.test");
}

#[test]
fn trusting_an_enabled_account_again_forgets_its_pins_and_syncs_it_and_a_disabled_one_waits() {
    let w = world(None);
    assert_eq!(w.connections.trust_cert(Some(" ada@example.test ")).unwrap(), Trusted::SyncAgain(ADA.into()));
    assert_eq!(w.connections.trust_cert(Some("bea@example.test")).unwrap(), Trusted::Waits("bea@example.test".into()));
    let accounts = w.file.read().unwrap()["accounts"].clone();
    assert!(accounts.as_array().unwrap().iter().all(|a| a.get("tls_pins").is_none()));
    assert_eq!(
        *w.heard.0.lock().unwrap(),
        [AccountChange::Changed(ADA.into()), AccountChange::Changed("bea@example.test".into())]
    );
    assert_eq!(w.connections.trust_cert(Some("zed@example.test")).unwrap_err(), Problem::not_found("not found").into());
    assert_eq!(w.connections.trust_cert(None).unwrap_err(), Problem::not_found("not found").into());
}

#[test]
fn each_change_is_told_once_the_file_holds_it() {
    let w = world(None);
    let dee = json!({"email": "dee@example.test", "host": "h.example.test", "credential": typed("dee-password")});
    assert_eq!(w.connections.add(&object(dee)).unwrap()["account"]["email"], "dee@example.test");
    assert_eq!(
        w.connections.edit(&object(json!({"email": "DEE@example.test", "alias": "Dee"}))).unwrap()["account"]["alias"],
        "Dee"
    );
    assert_eq!(
        w.connections.delete(&object(json!({"email": "DEE@example.test"}))).unwrap(),
        json!({"ok": true, "removed": "DEE@example.test"})
    );
    assert_eq!(
        *w.heard.0.lock().unwrap(),
        [
            AccountChange::Added("dee@example.test".into()),
            AccountChange::Changed("dee@example.test".into()),
            AccountChange::Removed("dee@example.test".into()),
        ]
    );
    let not_found = Problem::not_found("not found").into();
    assert_eq!(w.connections.edit(&object(json!({"email": "dee@example.test"}))).unwrap_err(), not_found);
    assert_eq!(w.connections.delete(&object(json!({"email": "dee@example.test"}))).unwrap_err(), not_found);
    assert_eq!(w.heard.0.lock().unwrap().len(), 3);
}

fn draft(fields: Value) -> Map<String, Value> {
    let mut draft = object(
        json!({"email": "dee@example.test", "type": "imap", "host": "imap.example.test", "credential": typed("typed")}),
    );
    draft.extend(object(fields));
    draft
}

#[test]
fn a_draft_naming_anything_but_a_typed_password_is_refused() {
    let save_first = "save the account to test a stored secret; before then only a typed password is tried";
    for credential in [
        json!({"style": "op", "ref": "op://Private/Dee/password"}),
        json!({"style": "env", "var": "MAILMIND_TEST_DEE_PASSWORD"}),
        json!({"style": "proton_bridge"}),
        json!({"op": "op://Private/Dee/password"}),
        json!({"plain": "stored-as-the-file-holds-it"}),
        json!({"keychain": "account:dee@example.test"}),
        json!({"style": "plain", "value": "typed-password"}),
        json!({"style": "keychain", "value": 123}),
        json!({"style": "keychain"}),
        json!({"style": "keychain", "value": ""}),
        json!({}),
        json!(null),
        json!("typed-password"),
    ] {
        let refused = throwaway_account(&draft(json!({"credential": credential}))).unwrap_err();
        assert_eq!(refused, NotTried::Refused(save_first), "{credential}");
    }
}

#[test]
fn what_a_draft_leaves_out_comes_from_its_type() {
    for (kind, host, port, starttls) in [
        ("gmail", "imap.gmail.com", 993, false),
        ("proton", "127.0.0.1", 1143, true),
        ("pop3", "imap.example.test", 993, false),
    ] {
        let mut typed = draft(json!({"type": kind}));
        if kind != "pop3" {
            typed.remove("host");
        }
        let tried = throwaway_account(&typed).unwrap();
        assert_eq!((tried.host.as_deref(), tried.port, tried.starttls), (Some(host), port, starttls), "{kind}");
    }
    let tried = throwaway_account(&draft(
        json!({"type": "proton", "host": "bridge.example.test", "port": "1144", "starttls": null}),
    ))
    .unwrap();
    assert_eq!((tried.host.as_deref(), tried.port, tried.starttls), (Some("bridge.example.test"), 1144, false));
}

#[test]
fn a_generic_draft_needs_a_host_typed_and_hears_of_its_port_first() {
    for host in [json!(null), json!("")] {
        assert_eq!(
            throwaway_account(&draft(json!({"host": host}))).unwrap_err(),
            NotTried::Refused("a host is required")
        );
    }
    let bare = object(json!({"email": "dee@example.test", "type": "imap"}));
    assert_eq!(throwaway_account(&bare).unwrap_err(), NotTried::Refused("a host is required"));
    let bad_port = draft(json!({"host": "", "port": 0.5, "credential": null}));
    assert_eq!(throwaway_account(&bad_port).unwrap_err(), NotTried::BadPort);
    assert_eq!(throwaway_account(&draft(json!({"host": 7}))).unwrap().host, None);
}
