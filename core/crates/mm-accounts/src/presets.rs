//! Which preset an address's account takes, from its domain (`mailmind.accounts.domain.preset` and
//! `application.presets`): a provider's own domains take its preset, which the form chooses (`sure`), and a domain whose
//! mail a provider's servers take (a Google Workspace or Fastmail domain's, say), as its MX records name them, is
//! offered it. An offer is only that: the person chooses, and nothing is guessed that they can't undo.
//!
//! A preset says what the form needs of its provider: the type and servers its account takes, a line said before
//! the password (an app password asked for, Proton Bridge needed), and the page where that password is made. A
//! provider MailMind can't log in to yet (Microsoft's, which needs its own sign-in, #47) is named, and has no servers.
//!
//! A domain is looked up only when it reads as one a lookup could ask about: ASCII labels of letters, digits and
//! hyphens, two or more of them, at most 253 characters. Anything else, a name in another script included, is no
//! domain here, and offers nothing.

use std::sync::Arc;

use mm_kernel::errors::{Failure, Problem};
use serde_json::{Value, json};

use crate::ports::MailHosts;

/// Google's own addresses: their accounts are Gmail's.
pub const GOOGLE_DOMAINS: [&str; 2] = ["gmail.com", "googlemail.com"];
const LONGEST_DOMAIN: usize = 253;

/// One server of a preset: host, port, and whether TLS starts with STARTTLS (else from the first byte).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Server {
    pub host: &'static str,
    pub port: u16,
    pub starttls: bool,
}

/// What an account of a preset is saved as: its type, and for a generic IMAP one its servers and Trash.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Settings {
    pub kind: &'static str,
    pub imap: Option<Server>,
    pub smtp: Option<Server>,
    pub trash: Option<&'static str>,
}

/// A page a person is sent to, and the words of its link.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Link {
    pub url: &'static str,
    pub words: &'static str,
}

/// A provider MailMind knows.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Provider {
    /// Its preset's name, which the answer gives.
    pub preset: &'static str,
    /// Its name as a person knows it.
    pub name: &'static str,
    /// Its own domains.
    pub domains: &'static [&'static str],
    /// A brand whose domain under any country's ending is its own (`yahoo` for yahoo.com, yahoo.co.uk, yahoo.fr).
    pub brands: &'static [&'static str],
    /// An MX record naming one of these, or a host under it, is its servers'.
    pub mail_hosts: &'static [&'static str],
    /// What its account is saved as; None for one MailMind can't log in to yet.
    pub settings: Option<Settings>,
    /// What the form says before the password.
    pub note: Option<&'static str>,
    /// Where its password for MailMind is made, or what it needs is got.
    pub link: Option<Link>,
}

const fn server(host: &'static str, port: u16, starttls: bool) -> Option<Server> {
    Some(Server { host, port, starttls })
}

/// Where a Google Account makes an App Password.
pub const GOOGLE_APP_PASSWORDS: Link =
    Link { url: "https://myaccount.google.com/apppasswords", words: "Make an App Password" };
/// Where Proton Bridge is got.
pub const PROTON_BRIDGE: Link = Link { url: "https://proton.me/mail/bridge", words: "Get Proton Bridge" };

/// Every provider MailMind knows, in the order a domain is matched against them.
pub const PROVIDERS: [Provider; 7] = [
    Provider {
        preset: "gmail",
        name: "Gmail",
        domains: &GOOGLE_DOMAINS,
        brands: &[],
        mail_hosts: &["google.com", "googlemail.com"],
        settings: Some(Settings { kind: "gmail", imap: None, smtp: None, trash: None }),
        note: None,
        link: Some(GOOGLE_APP_PASSWORDS),
    },
    Provider {
        preset: "icloud",
        name: "iCloud",
        domains: &["icloud.com", "me.com", "mac.com"],
        brands: &[],
        mail_hosts: &[],
        settings: Some(Settings {
            kind: "imap",
            imap: server("imap.mail.me.com", 993, false),
            smtp: server("smtp.mail.me.com", 587, true),
            trash: Some("Deleted Messages"),
        }),
        note: Some(
            "iCloud Mail takes an app-specific password, not your Apple Account password: make one at \
             account.apple.com, under Sign-In and Security, then paste it here.",
        ),
        link: Some(Link { url: "https://account.apple.com", words: "Make an app-specific password" }),
    },
    Provider {
        preset: "yahoo",
        name: "Yahoo",
        domains: &["ymail.com", "rocketmail.com"],
        brands: &["yahoo"],
        mail_hosts: &[],
        settings: Some(Settings {
            kind: "imap",
            imap: server("imap.mail.yahoo.com", 993, false),
            smtp: server("smtp.mail.yahoo.com", 465, false),
            trash: None,
        }),
        note: Some(
            "Yahoo Mail takes an app password, not your Yahoo password: make one in your Yahoo account's security \
             settings, then paste it here.",
        ),
        link: Some(Link { url: "https://login.yahoo.com/account/security", words: "Make an app password" }),
    },
    Provider {
        preset: "aol",
        name: "AOL",
        domains: &["aim.com"],
        brands: &["aol"],
        mail_hosts: &[],
        settings: Some(Settings {
            kind: "imap",
            imap: server("imap.aol.com", 993, false),
            smtp: server("smtp.aol.com", 465, false),
            trash: None,
        }),
        note: Some(
            "AOL Mail takes an app password, not your AOL password: make one in your AOL account's security \
             settings, then paste it here.",
        ),
        link: Some(Link { url: "https://login.aol.com/account/security", words: "Make an app password" }),
    },
    Provider {
        preset: "fastmail",
        name: "Fastmail",
        domains: &["fastmail.com", "fastmail.fm"],
        brands: &[],
        mail_hosts: &["messagingengine.com"],
        settings: Some(Settings {
            kind: "imap",
            imap: server("imap.fastmail.com", 993, false),
            smtp: server("smtp.fastmail.com", 465, false),
            trash: None,
        }),
        note: Some(
            "Fastmail takes an app password, not your Fastmail password: make one in Fastmail's settings, under \
             Privacy & Security, then paste it here.",
        ),
        link: Some(Link {
            url: "https://www.fastmail.help/hc/en-us/articles/360058752854",
            words: "How to make an app password",
        }),
    },
    Provider {
        preset: "microsoft",
        name: "Microsoft",
        domains: &["msn.com", "passport.com"],
        brands: &["outlook", "hotmail", "live"],
        mail_hosts: &["protection.outlook.com"],
        settings: None,
        note: Some(
            "Outlook, Hotmail, Live and Microsoft 365 accounts need Microsoft's own sign-in, which MailMind doesn't \
             support yet.",
        ),
        link: None,
    },
    Provider {
        preset: "proton",
        name: "Proton",
        domains: &["proton.me", "protonmail.com", "protonmail.ch", "pm.me"],
        brands: &[],
        mail_hosts: &["protonmail.ch"],
        settings: Some(Settings { kind: "proton", imap: None, smtp: None, trash: None }),
        note: Some(
            "Proton Mail works through Proton Bridge, on a paid Proton plan: install Bridge, sign in to it, and keep \
             it running while MailMind is open.",
        ),
        link: Some(PROTON_BRIDGE),
    },
];

/// The second-level labels a country's ending may put before its own (yahoo.co.uk, yahoo.com.au).
const SECOND_LEVELS: [&str; 4] = ["co", "com", "ne", "or"];

fn is_label(label: &str) -> bool {
    let bytes = label.as_bytes();
    let inner = |b: &u8| b.is_ascii_lowercase() || b.is_ascii_digit() || *b == b'-';
    let edge = |b: Option<&u8>| b.is_some_and(|b| b.is_ascii_lowercase() || b.is_ascii_digit());
    (1..=63).contains(&bytes.len()) && bytes.iter().all(inner) && edge(bytes.first()) && edge(bytes.last())
}

/// The domain an address names, in lower case and without a final dot, when it reads as one; else None.
pub fn domain_of(email: &str) -> Option<String> {
    let (_, domain) = mm_kernel::text::strip(email).rsplit_once('@')?;
    if !domain.is_ascii() {
        return None;
    }
    let domain = domain.to_ascii_lowercase();
    let domain = domain.strip_suffix('.').unwrap_or(&domain);
    let labels: Vec<&str> = domain.split('.').collect();
    if domain.len() > LONGEST_DOMAIN || labels.len() < 2 || !labels.iter().all(|label| is_label(label)) {
        return None;
    }
    Some(domain.to_owned())
}

/// Whether `domain` is `brand` under a country's or the world's ending: brand.tld, or brand.co.cc and the like.
fn under_brand(domain: &str, brand: &str) -> bool {
    let labels: Vec<&str> = domain.split('.').collect();
    match labels.as_slice() {
        [first, _] => *first == brand,
        [first, second, country] => *first == brand && SECOND_LEVELS.contains(second) && country.len() == 2,
        _ => false,
    }
}

/// The provider whose own domain `domain` is, if one's is.
pub fn provider_of_domain(domain: &str) -> Option<&'static Provider> {
    PROVIDERS.iter().find(|p| p.domains.contains(&domain) || p.brands.iter().any(|brand| under_brand(domain, brand)))
}

/// Whether an MX record's host is one of `provider`'s mail servers.
pub fn names_provider(host: &str, provider: &Provider) -> bool {
    let host = host.to_ascii_lowercase();
    let host = host.strip_suffix('.').unwrap_or(&host);
    provider.mail_hosts.iter().any(|known| host == *known || host.ends_with(&format!(".{known}")))
}

/// Whether an MX record's host is one of Google's mail servers.
pub fn names_google(host: &str) -> bool {
    names_provider(host, &PROVIDERS[0])
}

/// The provider whose servers an IMAP host is, as a preset names them.
pub fn provider_of_host(host: &str) -> Option<&'static Provider> {
    let host = host.to_ascii_lowercase();
    PROVIDERS.iter().find(|p| p.settings.and_then(|s| s.imap).is_some_and(|imap| imap.host == host))
}

/// The preset an address's account takes, as Settings asks while the address is typed.
#[derive(Debug)]
pub struct Presets {
    hosts: Arc<dyn MailHosts>,
}

impl Presets {
    pub fn new(hosts: Arc<dyn MailHosts>) -> Presets {
        Presets { hosts }
    }

    /// A provider's preset, `sure` for an address at one of its own domains, which the form chooses, and offered
    /// (`sure` false) for one whose domain's MX records name its servers; none for any other, a domain no lookup
    /// could ask about, or one whose lookup found nothing. No address at all is a bad request.
    pub fn preset(&self, email: Option<&str>) -> Result<Value, Failure> {
        let email = email.map(mm_kernel::text::strip).filter(|e| !e.is_empty()).ok_or_else(Problem::bad_request)?;
        let Some(domain) = domain_of(email) else { return Ok(answer(None, None, false)) };
        if let Some(provider) = provider_of_domain(&domain) {
            return Ok(answer(Some(&domain), Some(provider), true));
        }
        let hosts = self.hosts.of(&domain);
        let offered = PROVIDERS.iter().find(|p| hosts.iter().any(|host| names_provider(host, p)));
        Ok(answer(Some(&domain), offered, false))
    }
}

fn server_json(server: Option<Server>) -> (Value, Value, Value) {
    match server {
        Some(s) => (json!(s.host), json!(s.port), json!(s.starttls)),
        None => (Value::Null, Value::Null, Value::Null),
    }
}

/// A preset's settings as the answer gives them: every field the account body takes, null where its type gives it.
pub fn settings_json(settings: &Settings) -> Value {
    let (host, port, starttls) = server_json(settings.imap);
    let (smtp_host, smtp_port, smtp_starttls) = server_json(settings.smtp);
    json!({"type": settings.kind, "host": host, "port": port, "starttls": starttls, "smtp_host": smtp_host,
           "smtp_port": smtp_port, "smtp_starttls": smtp_starttls, "trash": settings.trash})
}

/// A link as an answer gives it.
pub fn link_json(link: Option<Link>) -> Value {
    link.map_or(Value::Null, |l| json!({"url": l.url, "words": l.words}))
}

fn answer(domain: Option<&str>, provider: Option<&Provider>, sure: bool) -> Value {
    json!({
        "ok": true,
        "domain": domain,
        "preset": provider.map(|p| p.preset),
        "sure": sure,
        "name": provider.map(|p| p.name),
        "supported": provider.is_none_or(|p| p.settings.is_some()),
        "settings": provider.and_then(|p| p.settings.as_ref()).map(settings_json),
        "note": provider.and_then(|p| p.note),
        "link": link_json(provider.and_then(|p| p.link)),
    })
}
