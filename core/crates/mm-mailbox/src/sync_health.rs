//! When an account's failed sync shows as one that can't connect (`mailbox.domain.sync_health`). Trouble that may
//! pass, what a sleep or a change of network leaves for a moment, shows only once the next sync of the account has
//! failed too, or once the first failure in a row is three minutes old. Any other, a password refused, a certificate
//! changed or a panic, shows at once. A sync that works ends the run. A run may begin before any sync has failed,
//! with a connection to the server that couldn't be made outside one (`Failing::heard`): it counts from then.
//!
//! While a run of trouble that may pass lasts, the account's server is tried again every `RETRY_SECONDS`, so the
//! sync that ends it comes within moments of the server answering again, not at the next round, minutes on. A try
//! that fails changes nothing: it is no sync of the round's, and counts toward nothing.

/// The kinds of trouble that may pass (`error_kind`'s): a connection lost or refused, a name not found, a server that
/// didn't answer in time.
pub const PASSING: [&str; 2] = ["NETWORK", "TIMEOUT"];
/// A login its server said it can't take for now ([UNAVAILABLE]), which says nothing of the password.
pub const UNAVAILABLE: &str = "LoginUnavailable";
/// The failed syncs in a row at which trouble that may pass shows.
pub const SHOWS_AFTER_ROUNDS: u32 = 2;
/// The seconds since the first failure in a row at which trouble that may pass shows.
pub const SHOWS_AFTER_SECONDS: i64 = 180;
/// The seconds between tries of an account's server while its trouble may pass.
pub const RETRY_SECONDS: i64 = 20;

/// Whether a failure of `class`, of `kind`, is trouble that may pass (`accounts.may_pass`): one of the `PASSING`
/// kinds, or a login its server couldn't take for now. A login it refused never is.
pub fn may_pass(class: &str, kind: &str) -> bool {
    PASSING.contains(&kind) || class == UNAVAILABLE
}

/// An account's syncs failed in a row, until one works.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Failing {
    /// How many have failed in a row.
    pub rounds: u32,
    /// When the first of them failed, in the clock's seconds.
    pub since: i64,
    /// The last one's class.
    pub class: &'static str,
    /// Whether the last one may pass.
    pub passing: bool,
}

impl Failing {
    /// The run with one more failure, at `now`: the first of a run when there was none.
    pub fn and(run: Option<&Failing>, class: &'static str, passing: bool, now: i64) -> Failing {
        match run {
            Some(run) => Failing { rounds: run.rounds + 1, since: run.since, class, passing },
            None => Failing { rounds: 1, since: now, class, passing },
        }
    }

    /// A run begun at `now` by trouble that may pass, heard outside a sync (a connection to the server that couldn't
    /// be made): no sync has failed in it yet, so the next that fails is its first.
    pub fn heard(class: &'static str, now: i64) -> Failing {
        Failing { rounds: 0, since: now, class, passing: true }
    }

    /// Whether the run shows as one that can't connect, at `now`.
    pub fn shows(&self, now: i64) -> bool {
        shows(self.passing, self.rounds, now - self.since)
    }
}

/// Whether a failure, the `rounds`th in a row, the first `since` seconds ago, shows: at once, unless it may pass.
pub fn shows(passing: bool, rounds: u32, since: i64) -> bool {
    !passing || rounds >= SHOWS_AFTER_ROUNDS || since >= SHOWS_AFTER_SECONDS
}
