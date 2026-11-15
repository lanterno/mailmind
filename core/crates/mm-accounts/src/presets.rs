//! Which preset an address's account takes, from its domain (`mailmind.accounts.domain.preset` and
//! `application.presets`): Gmail's for Google's own domains, and Gmail's offered for a domain whose mail Google's
//! servers take (a Google Workspace domain's), as its MX records name them. An offer is only that: the person chooses,
//! and nothing is guessed that they can't undo.
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
/// An MX record naming one of these, or a host under it, is Google's.
const GOOGLE_MAIL_HOSTS: [&str; 2] = ["google.com", "googlemail.com"];
const LONGEST_DOMAIN: usize = 253;

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

/// Whether an MX record's host is one of Google's mail servers.
pub fn names_google(host: &str) -> bool {
    let host = host.to_ascii_lowercase();
    let host = host.strip_suffix('.').unwrap_or(&host);
    GOOGLE_MAIL_HOSTS.iter().any(|google| host == *google || host.ends_with(&format!(".{google}")))
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

    /// Gmail's preset, `sure` for an address at one of Google's own domains, which the form chooses, and offered
    /// (`sure` false) for one whose domain's MX records name Google's servers; none for any other, a domain no lookup
    /// could ask about, or one whose lookup found nothing. No address at all is a bad request.
    pub fn preset(&self, email: Option<&str>) -> Result<Value, Failure> {
        let email = email.map(mm_kernel::text::strip).filter(|e| !e.is_empty()).ok_or_else(Problem::bad_request)?;
        let Some(domain) = domain_of(email) else { return Ok(answer(None, None, false)) };
        if GOOGLE_DOMAINS.contains(&domain.as_str()) {
            return Ok(answer(Some(&domain), Some("gmail"), true));
        }
        let google = self.hosts.of(&domain).iter().any(|host| names_google(host));
        Ok(answer(Some(&domain), google.then_some("gmail"), false))
    }
}

fn answer(domain: Option<&str>, preset: Option<&str>, sure: bool) -> Value {
    json!({"ok": true, "domain": domain, "preset": preset, "sure": sure})
}
