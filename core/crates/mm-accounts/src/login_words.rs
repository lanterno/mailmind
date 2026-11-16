//! What a test of an account's login that didn't work says to a person (`mailmind.accounts.domain.login_words`): a
//! sentence that says what went wrong and what to do next, and the page that helps, by the kind of trouble
//! `connections::quiet` named. The class stays in the answer's `error`, for the owner.
//!
//! The words come from `quiet`'s, never a server's or an error's, so both servers say the same of the same trouble: a
//! password refused (and the provider's page where an app password is made), a server that didn't answer, a
//! certificate not trusted, Proton Bridge not running. A trouble at the outgoing (SMTP) server, met only once the
//! incoming one let MailMind in, says reading mail works.

use crate::connections::{GMAIL_APP_PASSWORD, GMAIL_IMAP_OFF, GMAIL_REFUSED, GMAIL_WEB_SIGN_IN};
use crate::draft::{BAD_PORT, BAD_SMTP_PORT};
use crate::presets::{self, Link, Provider};

/// What the person is told, and where they may go next.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Said {
    pub says: String,
    pub link: Option<Link>,
}

/// The account a test tried, as far as its words need it: its type, its IMAP host, and its address.
#[derive(Clone, Copy, Debug, Default)]
pub struct Whose<'a> {
    pub kind: Option<&'a str>,
    pub host: Option<&'a str>,
    pub email: Option<&'a str>,
}

/// The provider an account is at: by its type, then its IMAP host, then its address's domain.
fn provider(whose: Whose<'_>) -> Option<&'static Provider> {
    if let Some(kind @ ("gmail" | "proton")) = whose.kind {
        return presets::PROVIDERS.iter().find(|p| p.preset == kind);
    }
    whose
        .host
        .and_then(presets::provider_of_host)
        .or_else(|| whose.email.and_then(presets::domain_of).and_then(|d| presets::provider_of_domain(&d)))
}

fn said(says: impl Into<String>, link: Option<Link>) -> Said {
    Said { says: says.into(), link }
}

/// What a test's trouble says: `error` is `quiet`'s word for it, at the incoming server, or at the outgoing one when
/// `outgoing`.
pub fn said_of(error: &str, whose: Whose<'_>, outgoing: bool) -> Said {
    let at = provider(whose);
    let proton = at.is_some_and(|p| p.preset == "proton");
    match error {
        // Gmail's refusals are said in sentences of their own already: the App Password ones send to Google's page.
        GMAIL_REFUSED | GMAIL_APP_PASSWORD => said(error, Some(presets::GOOGLE_APP_PASSWORDS)),
        GMAIL_WEB_SIGN_IN | GMAIL_IMAP_OFF => said(error, None),
        BAD_PORT => said("The port must be a number from 1 to 65535.", None),
        BAD_SMTP_PORT => said("The outgoing server's port must be a number from 1 to 65535.", None),
        "connection refused" if proton && !outgoing => said(
            "Proton Bridge isn't running. Open Bridge and wait until it says it is connected, then test again.",
            Some(presets::PROTON_BRIDGE),
        ),
        "certificate changed" => said(
            "The mail server on this Mac showed a different certificate, so MailMind didn't send your password. If \
             you reinstalled or reset Proton Bridge, trust the new one in Settings.",
            None,
        ),
        "AUTHENTICATIONFAILED" if outgoing => said(
            "Reading mail works, but the outgoing server didn't accept the password. Check the outgoing server's \
             settings.",
            None,
        ),
        "AUTHENTICATIONFAILED" if proton => said(
            "Proton Bridge didn't accept that password. Use the password Bridge shows for this address, not your \
             Proton password.",
            Some(presets::PROTON_BRIDGE),
        ),
        "AUTHENTICATIONFAILED" => match at {
            // Microsoft's accounts take its own sign-in, which no password can stand in for.
            Some(p) if p.settings.is_none() => said(p.note.unwrap_or_default(), None),
            Some(p) if p.link.is_some() => said(
                format!(
                    "{name} didn't accept that password. {name} takes an app password, not your account's own \
                     password: make one, then paste it here.",
                    name = p.name
                ),
                p.link,
            ),
            _ => said(
                "The server didn't accept that address and password. Check them; if your provider offers app \
                 passwords, make one and paste it here.",
                None,
            ),
        },
        "connection refused" | "timeout" | "host not found" if outgoing => {
            said("Reading mail works, but the outgoing server didn't answer. Check its name and port.", None)
        }
        "connection refused" | "timeout" | "host not found" => {
            said("The server didn't answer. Check the server's name and port, and that this Mac is online.", None)
        }
        "SSLCertVerificationError" if outgoing => said(
            "Reading mail works, but the outgoing server's certificate isn't trusted, so MailMind didn't send your \
             password. Check its name.",
            None,
        ),
        "SSLCertVerificationError" => said(
            "The server's certificate isn't trusted, so MailMind didn't send your password. Check the server's name: \
             use the one your provider gives.",
            None,
        ),
        "SSLError" if outgoing => said(
            "Reading mail works, but a secure connection to the outgoing server couldn't be made. Check its port and \
             whether it wants TLS or STARTTLS.",
            None,
        ),
        "SSLError" => said(
            "A secure connection couldn't be made. Check the port, and whether the server wants TLS or STARTTLS.",
            None,
        ),
        _ if outgoing => {
            said("Reading mail works, but MailMind couldn't log in to the outgoing server. Check its settings.", None)
        }
        _ => said("MailMind couldn't log in. Check the address, the password and the server's settings.", None),
    }
}
