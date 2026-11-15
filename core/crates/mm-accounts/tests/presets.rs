//! Which preset an address's account takes (`presets`), as tests/unit/accounts/test_presets.py holds the Python app's:
//! Gmail's for Google's own domains, offered for a domain whose MX records name Google's mail servers, none for any
//! other, through the MailHosts port and no network.

#![allow(clippy::unwrap_used)]

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use mm_accounts::ports::MailHosts;
use mm_accounts::presets::{Presets, domain_of, names_google};
use mm_kernel::errors::{Failure, Problem};
use serde_json::{Value, json};

#[test]
fn an_address_names_a_domain_a_lookup_could_ask_about_or_none() {
    let long = ["a".repeat(63), "a".repeat(63), "a".repeat(63)].join(".") + ".test";
    let too_long = ["a".repeat(63), "a".repeat(63), "a".repeat(63), "a".repeat(63)].join(".") + ".test";
    for (email, domain) in [
        ("ada@gmail.com", Some("gmail.com")),
        ("  Ada@GMail.COM  ", Some("gmail.com")),
        ("ada@example.test.", Some("example.test")),
        ("ada@mail.acme-1.example.test", Some("mail.acme-1.example.test")),
        ("\"a@b\"@example.test", Some("example.test")),
        (&format!("ada@{long}"), Some(long.as_str())),
        ("ada", None),
        ("ada@", None),
        ("@", None),
        ("ada@localhost", None),
        ("ada@example..test", None),
        ("ada@.example.test", None),
        ("ada@-acme.example.test", None),
        ("ada@acme-.example.test", None),
        ("ada@acme_mail.example.test", None),
        (&format!("ada@{}.test", "a".repeat(64)), None),
        (&format!("ada@{too_long}"), None),
        ("ada@b\u{fc}cher.example.test", None),
        ("ada@\u{212a}acme.example.test", None), // the Kelvin sign, which lowers to an ASCII k
        ("ada@exam ple.test", None),
        ("ada@example.test/x", None),
    ] {
        assert_eq!(domain_of(email).as_deref(), domain, "{email:?}");
    }
}

#[test]
fn an_mx_host_is_googles_when_it_is_one_of_googles_mail_servers() {
    for (host, google) in [
        ("aspmx.l.google.com", true),
        ("ALT1.ASPMX.L.GOOGLE.COM.", true),
        ("smtp.google.com", true),
        ("google.com", true),
        ("gmail-smtp-in.l.google.com", true),
        ("mx.googlemail.com", true),
        ("google.com.example.test", false),
        ("notgoogle.com", false),
        ("aspmx.l.google.co", false),
        ("mx.example.test", false),
        ("", false),
    ] {
        assert_eq!(names_google(host), google, "{host:?}");
    }
}

/// MX records as a table holds them, and which domains were asked about.
#[derive(Debug, Default)]
struct Hosts {
    table: HashMap<String, Vec<String>>,
    asked: Mutex<Vec<String>>,
}

impl MailHosts for Hosts {
    fn of(&self, domain: &str) -> Vec<String> {
        self.asked.lock().unwrap().push(domain.to_owned());
        self.table.get(domain).cloned().unwrap_or_default()
    }
}

fn hosts(domain: &str, found: &[&str]) -> Arc<Hosts> {
    let table = HashMap::from([(domain.to_owned(), found.iter().map(|h| (*h).to_owned()).collect())]);
    Arc::new(Hosts { table, ..Hosts::default() })
}

fn answer(domain: Option<&str>, preset: Option<&str>, sure: bool) -> Value {
    json!({"ok": true, "domain": domain, "preset": preset, "sure": sure})
}

#[test]
fn an_address_at_googles_own_domain_takes_gmails_preset_with_no_lookup() {
    for (email, domain) in
        [("ada@gmail.com", "gmail.com"), ("ada@googlemail.com", "googlemail.com"), ("Ada@GMAIL.com.", "gmail.com")]
    {
        let found = Arc::new(Hosts::default());
        assert_eq!(Presets::new(found.clone()).preset(Some(email)).unwrap(), answer(Some(domain), Some("gmail"), true));
        assert!(found.asked.lock().unwrap().is_empty());
    }
}

#[test]
fn a_domain_whose_mail_googles_servers_take_is_offered_gmails_preset() {
    let found = hosts("acme.example.test", &["mx.example.test", "aspmx.l.google.com"]);
    let offered = Presets::new(found.clone()).preset(Some("ada@Acme.Example.Test")).unwrap();
    assert_eq!(offered, answer(Some("acme.example.test"), Some("gmail"), false));
    assert_eq!(*found.asked.lock().unwrap(), ["acme.example.test"]);
}

#[test]
fn any_other_domain_is_offered_nothing() {
    for found in [&[][..], &["mx.example.test"], &[""], &["google.com.example.test"]] {
        let presets = Presets::new(hosts("acme.example.test", found));
        assert_eq!(
            presets.preset(Some("ada@acme.example.test")).unwrap(),
            answer(Some("acme.example.test"), None, false)
        );
    }
}

#[test]
fn an_address_with_no_domain_a_lookup_could_ask_about_asks_none() {
    for email in ["ada", "ada@localhost", "ada@b\u{fc}cher.example.test"] {
        let found = Arc::new(Hosts::default());
        assert_eq!(Presets::new(found.clone()).preset(Some(email)).unwrap(), answer(None, None, false));
        assert!(found.asked.lock().unwrap().is_empty());
    }
}

#[test]
fn no_address_at_all_is_a_bad_request() {
    for email in [None, Some(""), Some("   ")] {
        let refused = Presets::new(Arc::new(Hosts::default())).preset(email).unwrap_err();
        assert_eq!(refused, Failure::Problem(Problem::bad_request()), "{email:?}");
    }
}
