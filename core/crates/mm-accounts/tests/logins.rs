//! A login its server refused, held back (`Logins`): how long each hold lasts, which login it names, and what ends it,
//! on a clock the test moves (the Python app's tests/unit/accounts/test_refused_login.py). That nothing the server
//! does sends a held login, and what tells Logins of each refusal, login and change, is mm-server's
//! tests/login_hold.rs.

#![allow(clippy::unwrap_used)]

use std::sync::{Arc, Mutex};

use mm_accounts::Account;
use mm_accounts::login_hold::{FIRST, LONGEST, LoginKey};
use mm_accounts::logins::Logins;
use mm_accounts::ports::AccountChange;
use mm_kernel::seams::{Clock, Moment};
use serde_json::{Value, json};

/// A clock the test moves.
#[derive(Debug, Default)]
struct Moving(Mutex<i64>);

impl Moving {
    fn shift(&self, seconds: i64) {
        *self.0.lock().unwrap() += seconds * 1_000_000;
    }
}

impl Clock for Moving {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000 + *self.0.lock().unwrap())
    }
}

const ADA: &str = "ada@example.test";
const BEA: &str = "bea@example.test";

fn account(email: &str, changed: Value) -> Account {
    let mut entry = json!({"email": email, "host": "imap.example.test", "port": 993, "starttls": false,
                           "credential": {"keychain": format!("account:{email}"), "version": 1}});
    for (key, value) in changed.as_object().unwrap() {
        entry[key] = value.clone();
    }
    Account::from_entry(&entry).unwrap()
}

fn ada() -> Account {
    account(ADA, json!({}))
}

fn logins() -> (Logins, Arc<Moving>) {
    let clock = Arc::new(Moving::default());
    (Logins::new(LoginKey::new([7; 32]), clock.clone()), clock)
}

/// Its server refused `account`'s login.
fn refused(logins: &Logins, account: &Account) {
    logins.rejected(&account.email, logins.login_id(account));
}

#[test]
fn a_refusal_holds_that_login_back_for_the_first_rest() {
    let (logins, clock) = logins();
    assert!(!logins.held(&ada()));
    refused(&logins, &ada());
    clock.shift(FIRST - 1);
    assert!(logins.held(&ada()));
    clock.shift(2);
    assert!(!logins.held(&ada()));
}

#[test]
fn each_try_refused_after_a_hold_ended_waits_twice_as_long_up_to_an_hour() {
    let (logins, clock) = logins();
    refused(&logins, &ada());
    for rest in [FIRST, FIRST * 2, FIRST * 4, FIRST * 8, LONGEST, LONGEST] {
        clock.shift(rest - 1);
        assert!(logins.held(&ada()), "{rest}");
        clock.shift(2);
        assert!(!logins.held(&ada()), "{rest}");
        refused(&logins, &ada());
    }
}

#[test]
fn a_login_that_works_starts_the_count_again() {
    let (logins, clock) = logins();
    refused(&logins, &ada());
    clock.shift(FIRST + 1);
    refused(&logins, &ada()); // refused again: the next waits twice as long
    clock.shift(FIRST * 2 + 1);
    logins.accepted(ADA);
    refused(&logins, &ada());
    clock.shift(FIRST + 1);
    assert!(!logins.held(&ada()));
}

#[test]
fn another_server_address_or_credential_isnt_held() {
    // A new password goes at once, its credential a new version: the hold named the login that was refused.
    let (logins, _) = logins();
    refused(&logins, &ada());
    assert!(logins.held(&ada()));
    assert!(!logins.held(&account(ADA, json!({"credential": {"keychain": "account:ada@example.test", "version": 2}}))));
    assert!(!logins.held(&account(ADA, json!({"host": "imap2.example.test"}))));
    assert!(!logins.held(&account(BEA, json!({}))));
}

#[test]
fn the_account_changed_or_removed_lets_the_next_try_go_and_one_added_holds_nothing() {
    for change in [AccountChange::Changed(ADA.into()), AccountChange::Removed(ADA.into())] {
        let (logins, _) = logins();
        refused(&logins, &ada());
        refused(&logins, &account(BEA, json!({})));
        logins.changed(&change);
        assert!(!logins.held(&ada()) && logins.held(&account(BEA, json!({}))), "{change:?}");
    }
    let (logins, _) = logins();
    refused(&logins, &ada());
    logins.changed(&AccountChange::Added(ADA.into()));
    assert!(logins.held(&ada()));
}

#[test]
fn someone_asking_lets_the_next_try_go_and_a_refusal_then_holds_for_the_first_rest() {
    let (logins, clock) = logins();
    refused(&logins, &ada());
    clock.shift(FIRST + 1);
    refused(&logins, &ada());
    logins.forget(ADA);
    assert!(!logins.held(&ada()));
    refused(&logins, &ada());
    clock.shift(FIRST + 1);
    assert!(!logins.held(&ada()));
}
