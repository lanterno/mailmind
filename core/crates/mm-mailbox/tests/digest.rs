//! What `/api/digest` names as the accounts: the configured ones, in the config's order, whether or not any of their
//! mail is stored, so an account whose Inbox was filed empty, or whose first sync hasn't ended, keeps its place on the
//! rail.

use std::sync::Arc;

use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Moment};
use mm_kernel::zone::Utc;
use mm_mailbox::digest::{Digest, NotSyncing};
use mm_mailbox::ports::{AccountBook, AiStatus, DigestReads, Faces, StoredDigest};
use serde_json::{Value, json};

const ADA: &str = "ada@example.test";
const BEA: &str = "bea@example.test";
const CY: &str = "cy@example.test";

/// The accounts the config names, in its order.
#[derive(Debug)]
struct Configured(Vec<&'static str>);

impl AccountBook for Configured {
    fn emails(&self) -> Result<Vec<String>, Fault> {
        Ok(self.0.iter().map(|e| (*e).to_owned()).collect())
    }

    fn faces(&self) -> Result<Faces, Fault> {
        Ok(Faces::default())
    }
}

/// A database with no mail in it.
#[derive(Debug)]
struct NoMail;

impl DigestReads for NoMail {
    fn digest(&self, _limit: i64) -> Result<StoredDigest, Fault> {
        Ok(StoredDigest::default())
    }
}

#[derive(Debug)]
struct AiOff;

impl AiStatus for AiOff {
    fn report(&self) -> Result<Value, Fault> {
        Ok(json!({"engine": "none"}))
    }
}

#[derive(Debug)]
struct Frozen;

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_577_600_000_000)
    }
}

fn digest(accounts: Vec<&'static str>) -> Value {
    let digest = Digest::new(
        Arc::new(Configured(accounts)),
        Arc::new(NoMail),
        Arc::new(NotSyncing),
        Arc::new(AiOff),
        "full",
        Arc::new(Frozen),
        Arc::new(Utc),
    );
    digest.answer().expect("the digest")
}

#[test]
fn every_configured_account_is_named_though_none_of_its_mail_is_stored() {
    let answer = digest(vec![ADA, BEA]);
    assert_eq!(answer["accounts"], json!([ADA, BEA]), "a fresh database, or every Inbox filed empty");
    assert_eq!(answer["send_accounts"], json!([ADA, BEA]));
}

#[test]
fn the_accounts_come_in_the_configs_order() {
    assert_eq!(digest(vec![CY, ADA, BEA])["accounts"], json!([CY, ADA, BEA]), "as Settings lists them, not by name");
}

#[test]
fn with_no_account_configured_none_is_named() {
    assert_eq!(digest(vec![])["accounts"], json!([]));
}
