//! What counts as a refusal, which login a hold names, and how long it lasts (`login_hold`, as the Python app's
//! tests/unit/accounts/test_login_hold.py has it).

#![allow(clippy::unwrap_used)]

use mm_accounts::Account;
use mm_accounts::login_hold::{FIRST, Hold, LONGEST, LoginId, LoginKey, after_refusal, login_id, refuses};
use mm_kernel::seams::Moment;
use serde_json::{Value, json};

fn ada() -> Value {
    json!({"email": "ada@example.test", "host": "imap.example.test", "port": 993, "starttls": false,
           "strip_spaces": false, "credential": {"keychain": "account:ada@example.test", "version": 1},
           "alias": "Ada", "trash": "Trash"})
}

/// Ada's entry with `changed` over it.
fn with(changed: Value) -> Account {
    let mut entry = ada();
    for (key, value) in changed.as_object().unwrap() {
        entry[key] = value.clone();
    }
    Account::from_entry(&entry).unwrap()
}

fn id(account: &Account) -> LoginId {
    login_id(&LoginKey::new([7; 32]), account)
}

fn at(seconds: i64) -> Moment {
    Moment::from_micros(seconds * 1_000_000)
}

#[test]
fn a_server_saying_its_down_for_now_is_no_refusal_nor_is_a_login_lost_or_never_sent() {
    assert!(refuses("LoginRefused"));
    for class in ["LoginUnavailable", "Abort", "ConnectionReset", "Timeout", "Unsendable", "TLSPinMismatch"] {
        assert!(!refuses(class), "{class}");
    }
}

#[test]
fn a_login_is_the_same_login_whatever_else_the_account_says() {
    assert_eq!(id(&with(json!({}))), id(&with(json!({"alias": "Ada L.", "trash": "Bin", "signature": "Ada"}))));
}

#[test]
fn another_server_address_or_credential_is_another_login() {
    let ada = id(&with(json!({})));
    for changed in [
        json!({"email": "bea@example.test"}),
        json!({"host": "other.example.test"}),
        json!({"port": 143}),
        json!({"starttls": true}),
        json!({"strip_spaces": true}),
        json!({"credential": {"keychain": "account:ada@example.test", "version": 2}}),
        json!({"credential": {"op": "op://V/I/F"}}),
    ] {
        assert_ne!(ada, id(&with(changed.clone())), "{changed}");
    }
}

#[test]
fn a_logins_id_is_this_processs_own_and_says_nothing_of_it() {
    let ada = with(json!({}));
    assert_ne!(id(&ada), login_id(&LoginKey::new([8; 32]), &ada));
    let said = format!("{:?} {:?}", id(&ada), LoginKey::new([7; 32]));
    assert!(!said.contains("ada") && !said.contains("account") && !said.contains('7'), "{said}");
}

#[test]
fn a_first_refusal_holds_for_the_first_rest() {
    let login = id(&with(json!({})));
    assert_eq!(after_refusal(None, login, at(1000)), Hold { login, until: at(1000 + FIRST), rest: FIRST });
}

#[test]
fn a_refusal_after_a_hold_ended_doubles_it_up_to_the_longest() {
    let login = id(&with(json!({})));
    let (mut hold, mut now, mut rests) = (None, at(1000), Vec::new());
    for _ in 0..7 {
        let next = after_refusal(hold.as_ref(), login, now);
        rests.push(next.rest);
        now = next.until;
        hold = Some(next);
    }
    assert_eq!(rests, [300, 600, 1200, 2400, LONGEST, LONGEST, LONGEST]);
}

#[test]
fn two_tries_that_crossed_count_for_one() {
    let login = id(&with(json!({})));
    let first = after_refusal(None, login, at(1000));
    assert_eq!(after_refusal(Some(&first), login, at(1001)), Hold { login, until: at(1001 + FIRST), rest: FIRST });
}

#[test]
fn another_login_starts_again() {
    let (l, m) = (id(&with(json!({}))), id(&with(json!({"port": 143}))));
    let long = Hold { login: l, until: at(1000), rest: LONGEST };
    assert_eq!(after_refusal(Some(&long), m, at(2000)).rest, FIRST);
}

#[test]
fn a_hold_stops_only_its_login_and_only_until_it_ends() {
    let (l, m) = (id(&with(json!({}))), id(&with(json!({"port": 143}))));
    let hold = Hold { login: l, until: at(1000), rest: FIRST };
    assert!(hold.stops(l, at(999)));
    assert!(!hold.stops(l, at(1000)) && !hold.stops(m, at(999)));
}
