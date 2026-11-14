//! Which logins are held back after their server refused them (`mailmind.accounts.application.logins`, over
//! `login_hold`).
//!
//! Every session the server opens asks here before it connects (`held`), and a refusal its server answered is heard
//! here as CredentialsRejected (`rejected`). A hold ends when its time is up, when a login works (`accepted`), when
//! the account changes or goes (`changed`), and when someone asks to try now (`forget`). A start ends it too: nothing
//! here is durable, and one more try after a restart is no lockout.

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};

use mm_kernel::log::{self, Field};
use mm_kernel::seams::Clock;

use crate::Account;
use crate::login_hold::{self, Hold, LoginId, LoginKey};
use crate::ports::AccountChange;

const TAG: &str = "accounts";

#[derive(Debug)]
pub struct Logins {
    key: LoginKey,
    clock: Arc<dyn Clock>,
    /// Each address's last refusal's hold, kept past its end for the next.
    holds: Mutex<BTreeMap<String, Hold>>,
}

impl Logins {
    pub fn new(key: LoginKey, clock: Arc<dyn Clock>) -> Logins {
        Logins { key, clock, holds: Mutex::new(BTreeMap::new()) }
    }

    fn holds(&self) -> MutexGuard<'_, BTreeMap<String, Hold>> {
        // A hold is written whole or not at all, so one a panic left behind is as good as any.
        self.holds.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Which login `account` names (`login_hold.login_id`), under this process's key.
    pub fn login_id(&self, account: &Account) -> LoginId {
        login_hold::login_id(&self.key, account)
    }

    /// Whether a login as `account` must wait: nothing is to be sent to its server.
    pub fn held(&self, account: &Account) -> bool {
        let hold = self.holds().get(&account.email).copied();
        hold.is_some_and(|hold| hold.stops(self.login_id(account), self.clock.now()))
    }

    /// CredentialsRejected: hold `login`, `email`'s, back.
    pub fn rejected(&self, email: &str, login: LoginId) {
        let rest = {
            let mut holds = self.holds();
            let hold = login_hold::after_refusal(holds.get(email), login, self.clock.now());
            holds.insert(email.to_owned(), hold);
            hold.rest
        };
        log::warning(
            TAG,
            "its server refused a login, and the next try waits %(count)s seconds",
            &[(Field::Count, &rest.to_string())],
            None,
        );
    }

    /// A login as `email` worked.
    pub fn accepted(&self, email: &str) {
        self.forget(email);
    }

    /// An account changed or went: someone looked at it, so try what it says now. Its addition holds nothing.
    pub fn changed(&self, change: &AccountChange) {
        if let AccountChange::Changed(email) | AccountChange::Removed(email) = change {
            self.forget(email);
        }
    }

    /// Let `email`'s next login go, as when someone asks to try now.
    pub fn forget(&self, email: &str) {
        self.holds().remove(email);
    }
}
