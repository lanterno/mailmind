//! Which preset an address's account takes (`presets`), as tests/unit/accounts/test_presets.py holds the Python app's:
//! a provider's own domains take its preset, a domain whose MX records name a provider's mail servers is offered it,
//! and any other takes none, through the MailHosts port and no network.

#![allow(clippy::unwrap_used)]

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use mm_accounts::ports::MailHosts;
use mm_accounts::presets::{Presets, domain_of, names_google, provider_of_host};
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

/// What an answer says of which preset it is: the rest is what that preset says, which the tests below hold.
fn answer(domain: Option<&str>, preset: Option<&str>, sure: bool) -> Value {
    json!({"ok": true, "domain": domain, "preset": preset, "sure": sure})
}

fn which(answer: &Value) -> Value {
    json!({"ok": answer["ok"], "domain": answer["domain"], "preset": answer["preset"], "sure": answer["sure"]})
}

fn preset_of(email: &str) -> Value {
    Presets::new(Arc::new(Hosts::default())).preset(Some(email)).unwrap()
}

#[test]
fn an_address_at_googles_own_domain_takes_gmails_preset_with_no_lookup() {
    for (email, domain) in
        [("ada@gmail.com", "gmail.com"), ("ada@googlemail.com", "googlemail.com"), ("Ada@GMAIL.com.", "gmail.com")]
    {
        let found = Arc::new(Hosts::default());
        let said = Presets::new(found.clone()).preset(Some(email)).unwrap();
        assert_eq!(which(&said), answer(Some(domain), Some("gmail"), true));
        assert!(found.asked.lock().unwrap().is_empty());
    }
}

#[test]
fn a_domain_whose_mail_googles_servers_take_is_offered_gmails_preset() {
    let found = hosts("acme.example.test", &["mx.example.test", "aspmx.l.google.com"]);
    let offered = Presets::new(found.clone()).preset(Some("ada@Acme.Example.Test")).unwrap();
    assert_eq!(which(&offered), answer(Some("acme.example.test"), Some("gmail"), false));
    assert_eq!(*found.asked.lock().unwrap(), ["acme.example.test"]);
}

#[test]
fn any_other_domain_is_offered_nothing() {
    for found in [&[][..], &["mx.example.test"], &[""], &["google.com.example.test"]] {
        let presets = Presets::new(hosts("acme.example.test", found));
        assert_eq!(
            presets.preset(Some("ada@acme.example.test")).unwrap(),
            json!({"ok": true, "domain": "acme.example.test", "preset": null, "sure": false, "name": null,
                   "supported": true, "settings": null, "note": null, "link": null})
        );
    }
}

#[test]
fn an_address_with_no_domain_a_lookup_could_ask_about_asks_none() {
    for email in ["ada", "ada@localhost", "ada@b\u{fc}cher.example.test"] {
        let found = Arc::new(Hosts::default());
        assert_eq!(which(&Presets::new(found.clone()).preset(Some(email)).unwrap()), answer(None, None, false));
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

#[test]
fn a_providers_own_domains_take_its_preset_with_no_lookup() {
    for (email, preset) in [
        ("ada@icloud.com", "icloud"),
        ("ada@me.com", "icloud"),
        ("ada@mac.com", "icloud"),
        ("ada@yahoo.com", "yahoo"),
        ("ada@yahoo.co.uk", "yahoo"),
        ("ada@yahoo.fr", "yahoo"),
        ("ada@yahoo.com.au", "yahoo"),
        ("ada@yahoo.co.in", "yahoo"),
        ("ada@ymail.com", "yahoo"),
        ("ada@aol.com", "aol"),
        ("ada@aol.de", "aol"),
        ("ada@fastmail.com", "fastmail"),
        ("ada@fastmail.fm", "fastmail"),
        ("ada@outlook.com", "microsoft"),
        ("ada@hotmail.com", "microsoft"),
        ("ada@hotmail.co.uk", "microsoft"),
        ("ada@live.com", "microsoft"),
        ("ada@live.fr", "microsoft"),
        ("ada@msn.com", "microsoft"),
        ("ada@proton.me", "proton"),
        ("ada@protonmail.com", "proton"),
        ("ada@pm.me", "proton"),
    ] {
        let found = Arc::new(Hosts::default());
        let said = Presets::new(found.clone()).preset(Some(email)).unwrap();
        assert_eq!((said["preset"].as_str(), said["sure"].as_bool()), (Some(preset), Some(true)), "{email}");
        assert!(found.asked.lock().unwrap().is_empty(), "{email}");
    }
}

#[test]
fn a_brand_under_a_domain_that_isnt_a_countrys_ending_is_no_providers() {
    for email in [
        "ada@live.example.test",
        "ada@yahoo.example.test",
        "ada@mail.yahoo.com",
        "ada@notyahoo.com",
        "ada@aol.org",
        "ada@live.net",
        "ada@yahoo.co.u2",
        "ada@outlook.com.example",
    ] {
        assert_eq!(preset_of(email)["preset"], Value::Null, "{email}");
    }
}

#[test]
fn yahoo_japan_is_another_companys_with_servers_of_its_own() {
    for email in ["ada@yahoo.co.jp", "ada@yahoo.ne.jp", "ada@yahoo.jp"] {
        let found = Arc::new(Hosts::default());
        let said = Presets::new(found.clone()).preset(Some(email)).unwrap();
        assert_eq!((said["preset"].clone(), said["sure"].as_bool()), (Value::Null, Some(false)), "{email}");
        assert_eq!(found.asked.lock().unwrap().len(), 1, "{email}: looked up as any other domain");
    }
    assert_eq!(preset_of("ada@hotmail.co.jp")["preset"], "microsoft");
}

#[test]
fn icloud_says_its_servers_its_trash_and_where_its_app_specific_password_is_made() {
    assert_eq!(
        preset_of("ada@icloud.com"),
        json!({
            "ok": true, "domain": "icloud.com", "preset": "icloud", "sure": true, "name": "iCloud", "supported": true,
            "settings": {"type": "imap", "host": "imap.mail.me.com", "port": 993, "starttls": false,
                         "smtp_host": "smtp.mail.me.com", "smtp_port": 587, "smtp_starttls": true,
                         "trash": "Deleted Messages"},
            "note": "iCloud Mail takes an app-specific password, not your Apple Account password: make one at \
                     account.apple.com, under Sign-In and Security, then paste it here.",
            "link": {"url": "https://account.apple.com", "words": "Make an app-specific password"},
        })
    );
}

#[test]
fn yahoo_aol_and_fastmail_send_over_tls_from_the_first_byte_and_ask_for_an_app_password() {
    for (email, imap, smtp) in [
        ("ada@yahoo.com", "imap.mail.yahoo.com", "smtp.mail.yahoo.com"),
        ("ada@aol.com", "imap.aol.com", "smtp.aol.com"),
        ("ada@fastmail.com", "imap.fastmail.com", "smtp.fastmail.com"),
    ] {
        let said = preset_of(email);
        assert_eq!(
            said["settings"],
            json!({"type": "imap", "host": imap, "port": 993, "starttls": false, "smtp_host": smtp, "smtp_port": 465,
                   "smtp_starttls": false, "trash": null}),
            "{email}"
        );
        assert!(said["note"].as_str().unwrap().contains("app password"), "{email}");
        assert!(said["link"]["url"].as_str().unwrap().starts_with("https://"), "{email}");
    }
}

#[test]
fn gmail_and_proton_take_their_types_whose_defaults_give_the_servers() {
    let gmail = preset_of("ada@gmail.com");
    assert_eq!(gmail["settings"]["type"], "gmail");
    assert_eq!(gmail["settings"]["host"], Value::Null);
    assert_eq!(gmail["link"]["url"], "https://myaccount.google.com/apppasswords");
    let proton = preset_of("ada@proton.me");
    assert_eq!(proton["settings"]["type"], "proton");
    assert_eq!(proton["link"], json!({"url": "https://proton.me/mail/bridge", "words": "Get Proton Bridge"}));
    let note = proton["note"].as_str().unwrap();
    assert!(note.contains("Proton Bridge") && note.contains("paid") && note.contains("running"), "{note}");
}

#[test]
fn microsoft_is_named_and_unsupported_with_nothing_to_save() {
    let said = preset_of("ada@outlook.com");
    assert_eq!((said["name"].as_str(), said["supported"].as_bool()), (Some("Microsoft"), Some(false)));
    assert_eq!((said["settings"].clone(), said["link"].clone()), (Value::Null, Value::Null));
    assert!(said["note"].as_str().unwrap().contains("doesn't support yet"));
}

#[test]
fn a_domain_whose_mail_a_providers_servers_take_is_offered_its_preset() {
    for (mx, preset) in [
        ("in1-smtp.messagingengine.com", "fastmail"),
        ("mx01.mail.icloud.com", "icloud"),
        ("acme-example-test.mail.protection.outlook.com", "microsoft"),
        ("mail.protonmail.ch", "proton"),
        ("ASPMX.L.GOOGLE.COM.", "gmail"),
    ] {
        let found = hosts("acme.example.test", &["mx.example.test", mx]);
        let offered = Presets::new(found).preset(Some("ada@acme.example.test")).unwrap();
        assert_eq!(which(&offered), answer(Some("acme.example.test"), Some(preset), false), "{mx}");
    }
}

#[test]
fn a_presets_imap_host_names_its_provider() {
    assert_eq!(provider_of_host("IMAP.mail.me.com").map(|p| p.preset), Some("icloud"));
    assert_eq!(provider_of_host("imap.fastmail.com").map(|p| p.preset), Some("fastmail"));
    assert_eq!(provider_of_host("imap.example.test").map(|p| p.preset), None);
}
