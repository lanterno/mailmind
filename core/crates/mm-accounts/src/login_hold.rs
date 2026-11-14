//! A login its server refused isn't sent again straight away (`mailmind.accounts.domain.login_hold`).
//!
//! A server counts refused logins, and enough of them locks the account out, or the address they came from.
//! Everything that talks to a server opens its own connection (the sync, the change queue, reconcile, a person opening
//! a message), so a password gone bad would otherwise be sent every few minutes for as long as MailMind runs.
//!
//! So a refusal holds that login back for a while: five minutes, then twice that each time the next try is refused
//! too, up to an hour, and nothing is sent until then. A hold names one login, the server, the address and the
//! credential together, so another of any of them isn't held. A server saying it's down for now ([UNAVAILABLE], RFC
//! 5530) is no refusal, and neither is a connection lost during LOGIN.

use std::fmt;

use mm_kernel::seams::{Moment, Secret};
use sha2::{Digest, Sha256};

use crate::Account;

/// How long a first refusal holds its login back, in seconds.
pub const FIRST: i64 = 300;
/// The longest a hold lasts, in seconds.
pub const LONGEST: i64 = 3600;
/// The class of a login its server refused (`mm_imap`'s), which a login held back fails with too.
pub const REFUSED: &str = "LoginRefused";

/// Whether a login that didn't work was refused by its server, from the class of its trouble alone. A server saying
/// it can't for now (`LoginUnavailable`), a connection lost during LOGIN, and a login never sent are no refusal.
pub fn refuses(class: &str) -> bool {
    class == REFUSED
}

/// This process's own key for naming logins, drawn at its start, so a login's id says nothing of its credential to
/// anyone else.
pub struct LoginKey([u8; 32]);

impl LoginKey {
    pub fn new(bytes: [u8; 32]) -> LoginKey {
        LoginKey(bytes)
    }
}

impl fmt::Debug for LoginKey {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("LoginKey(..)")
    }
}

/// One way of logging in, so two can be compared without keeping a password to compare.
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct LoginId([u8; 32]);

impl fmt::Debug for LoginId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("LoginId(..)")
    }
}

/// The login `account` names: its address, its server and its credential, as the entry says each; never anything
/// else it says.
pub fn login_id(key: &LoginKey, account: &Account) -> LoginId {
    let what =
        (&account.email, &account.host, &account.port, account.starttls, account.strip_spaces, &account.credential);
    // A credential may hold the password itself: the text it is named by is wiped once it is read.
    let text = Secret::new(serde_json::to_string(&what).unwrap_or_default());
    LoginId(hmac(&key.0, text.expose().as_bytes()))
}

/// HMAC-SHA256 (RFC 2104) under a key of 32 bytes, which a block of 64 holds with zeros after it.
fn hmac(key: &[u8; 32], message: &[u8]) -> [u8; 32] {
    let mut block = [0u8; 64];
    block[..32].copy_from_slice(key);
    let inner = Sha256::new().chain_update(block.map(|b| b ^ 0x36)).chain_update(message).finalize();
    let outer = Sha256::new().chain_update(block.map(|b| b ^ 0x5c)).chain_update(inner).finalize();
    let mut out = [0u8; 32];
    out.copy_from_slice(&outer);
    out
}

/// A login held back: which, until when, and how long this hold was, which the next refusal doubles.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Hold {
    pub login: LoginId,
    pub until: Moment,
    /// In seconds.
    pub rest: i64,
}

impl Hold {
    /// Whether this hold stops `login` at `now`.
    pub fn stops(&self, login: LoginId, now: Moment) -> bool {
        login == self.login && now < self.until
    }
}

/// The hold a refusal of `login` at `now` leaves, given the one there was. Two tries that crossed, or a person's test
/// during a hold, count for one: only a try let through after a hold ended doubles it.
pub fn after_refusal(before: Option<&Hold>, login: LoginId, now: Moment) -> Hold {
    let rest = match before {
        Some(hold) if hold.login == login && now < hold.until => hold.rest,
        Some(hold) if hold.login == login => (hold.rest * 2).min(LONGEST),
        _ => FIRST,
    };
    Hold { login, until: Moment::from_micros(now.micros() + rest * 1_000_000), rest }
}

#[cfg(test)]
mod tests;
