//! What a request for a card asks (`application.asking`): the accounts it covers, checked, and which use case answers.
//! A GET only reads what is kept; a POST writes the one missing, and a refresh writes it again.

use std::sync::Arc;

use mm_kernel::seams::Clock;
use serde_json::Value;

use crate::brief::delta_since;
use crate::briefs::Briefs;
use crate::ports::{AccountList, Around};
use crate::refusals::AiFailure;
use crate::reviews::Reviews;
use crate::text::{python_str, strip, truthy};

/// `int(float(value or 0))`, or 0 where Python's raised: a tab's watermark, in seconds.
pub fn seconds_of(value: Option<&Value>) -> i64 {
    let Some(value) = value.filter(|v| truthy(v)) else { return 0 };
    let float = match value {
        Value::Bool(b) => Some(f64::from(u8::from(*b))),
        Value::Number(n) => n.as_f64(),
        Value::String(s) => strip(s).replace('_', "").parse::<f64>().ok(),
        _ => None,
    };
    float.filter(|f| f.is_finite()).map_or(0, |f| f.trunc() as i64)
}

#[derive(Debug)]
pub struct Asking {
    pub briefs: Arc<Briefs>,
    pub reviews: Arc<Reviews>,
    accounts: Arc<dyn AccountList>,
    around: Arc<dyn Around>,
    clock: Arc<dyn Clock>,
}

impl Asking {
    pub fn new(
        briefs: Arc<Briefs>,
        reviews: Arc<Reviews>,
        accounts: Arc<dyn AccountList>,
        around: Arc<dyn Around>,
        clock: Arc<dyn Clock>,
    ) -> Asking {
        Asking { briefs, reviews, accounts, around, clock }
    }

    /// The "Today" card: GET only reads it (`generate` false), POST writes it once, and a refresh again (`force`).
    pub fn briefing(&self, force: bool, generate: bool) -> Result<Value, AiFailure> {
        self.briefs.build(&self.accounts.emails()?, force, generate, "brief")
    }

    /// The Weekly Review: GET only reads it, POST writes it once, and a refresh again. For someone who has it written
    /// ahead on Monday morning, what is kept this week is read all week.
    pub fn review(&self, force: bool, generate: bool) -> Result<Value, AiFailure> {
        let week = self.around.weekly_review()?;
        self.reviews.build(&self.accounts.emails()?, force, generate, "review", week)
    }

    /// "Since you last looked", for the account named where it is one in use, else every one. `since` is seconds, held
    /// to a day back so a stale watermark never scans all history.
    pub fn delta(&self, account: Option<&Value>, since: Option<&Value>, generate: bool) -> Result<Value, AiFailure> {
        let enabled = self.accounts.emails()?;
        let named = account.filter(|a| truthy(a)).map(python_str);
        let accounts = match named {
            Some(one) if enabled.contains(&one) => vec![one],
            _ => enabled,
        };
        let now = self.clock.now().micros().div_euclid(1_000_000);
        self.briefs.build_delta(&accounts, delta_since(seconds_of(since), now), generate)
    }
}
