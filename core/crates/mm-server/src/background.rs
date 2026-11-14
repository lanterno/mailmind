//! The assistant's background work and Tidy's run (`bootstrap.background`'s assess, draft-ahead, brief-ahead,
//! review-ahead and tidy), each a round as Python's `schedule` runs it: triage of new mail; a reply drafted ahead for
//! the best mail that wants one, with its own breaker, while someone is at the screen; and, for someone who turned
//! each on, the morning's brief and the week's review written ahead and Tidy's run. Every AI call goes through the
//! Assistant's gateway and its cost governor.
//!
//! `Workers::start` puts them on lanes, unless the process only reads; `mm-served`'s `work assess` and `work tidy` run
//! one pass of the two the served suites wait on.

use std::sync::{Arc, Mutex, PoisonError};

use mm_accounts::Directory;
use mm_assistant::ahead::Ahead;
use mm_assistant::assessments::Assessments;
use mm_assistant::background_ports::{DraftCandidates, FinishedBatches};
use mm_assistant::calls::Calls;
use mm_assistant::prepared_replies::PreparedReplies;
use mm_assistant::prepared_reply::Breaker;
use mm_kernel::errors::{Failure, Fault};
use mm_kernel::events::Events;
use mm_kernel::seams::Clock;
use mm_kernel::zone::Zone;
use mm_mailbox::filing::Filing;
use mm_mailbox::tidying::{Tidying, TidyingParts};
use mm_store::Database;
use serde_json::Value;

use crate::workers::RoundOutcome;

/// Seconds between the assess job's rounds, unless a sync with new mail kicks it, and before its first.
pub const ASSESS_INTERVAL: i64 = 300;
pub const ASSESS_FIRST: i64 = 20;
/// The opt-in rounds: the brief's, the review's and Tidy's, each how often and how soon.
pub const BRIEF_AHEAD: (i64, i64) = (600, 90);
pub const REVIEW_AHEAD: (i64, i64) = (600, 100);
pub const TIDY: (i64, i64) = (900, 120);

/// The class of what went wrong, as a round's outcome names it.
fn class(failure: &Failure) -> &'static str {
    match failure {
        Failure::Fault(fault) => fault.class,
        Failure::Problem(_) => "Problem",
    }
}

/// What the background work is made from.
pub(crate) struct Parts<'a> {
    pub(crate) database: Arc<Database>,
    pub(crate) assistant: &'a crate::assistant_wiring::Assistant,
    pub(crate) directory: Arc<Directory>,
    pub(crate) filing: Arc<Filing>,
    pub(crate) events: Arc<dyn Events>,
    pub(crate) clock: Arc<dyn Clock>,
    pub(crate) zone: Arc<dyn Zone>,
    /// MAILMIND_NO_DRAFT_TICK off: replies are drafted ahead.
    pub(crate) drafts_ahead: bool,
}

#[derive(Debug)]
pub struct Background {
    /// Triage of the mail AI hasn't summarized.
    pub assessments: Arc<Assessments>,
    calls: Arc<Calls>,
    drafting: Arc<PreparedReplies>,
    /// Drafting ahead's three strikes, which outlive a round and not the process.
    breaker: Mutex<Breaker>,
    candidates: Arc<dyn DraftCandidates>,
    events: Arc<dyn Events>,
    ahead: Ahead,
    tidying: Tidying,
    directory: Arc<Directory>,
    /// The batches the forget round lets go of.
    batches: Arc<dyn FinishedBatches>,
    clock: Arc<dyn Clock>,
    /// Whether this process drafts replies ahead (MAILMIND_NO_DRAFT_TICK off).
    pub drafts_ahead: bool,
}

impl Background {
    pub(crate) fn assemble(parts: Parts<'_>) -> Background {
        let Parts { database, assistant, directory, filing, events, clock, zone, drafts_ahead } = parts;
        let assessments = Arc::new(Assessments::new(assistant.calls.clone(), database.clone(), clock.clone()));
        let ahead = Ahead::new(
            assistant.asking.clone(),
            Arc::new(crate::wiring::KnownAccounts(directory.clone())),
            database.clone(),
            clock.clone(),
            zone.clone(),
        );
        let tidying = Tidying::new(TidyingParts {
            log: database.clone(),
            digest: database.clone(),
            filing,
            clock: clock.clone(),
            zone,
        });
        Background {
            assessments,
            calls: assistant.calls.clone(),
            drafting: assistant.drafting.clone(),
            breaker: Mutex::new(Breaker::default()),
            candidates: database.clone(),
            events,
            ahead,
            tidying,
            directory,
            batches: database,
            clock,
            drafts_ahead,
        }
    }

    /// Why a call for `feature` would be held back now, or None when it would go.
    fn held_back(&self, feature: &str) -> Option<&'static str> {
        self.calls.gateway().held_back(feature, Some(&self.calls.model_label()))
    }

    /// A preference that is on or off, as the config file says it now.
    fn turned_on(&self, name: &str) -> Result<bool, &'static str> {
        let said = self.directory.preferences().map_err(|failure| class(&failure))?;
        Ok(said.get("preferences").and_then(|p| p.get(name)).and_then(Value::as_bool).unwrap_or(false))
    }

    /// The assess job's round: triage up to a round's worth of new mail. A full one says the next follows at once.
    pub fn assess_round(&self) -> RoundOutcome {
        self.assessments.round().map_err(|fault: Fault| fault.class)
    }

    /// A pre-drafter's round: at most one reply drafted ahead, only when the cost governor would let it go (nobody is
    /// using AI, someone was here lately, the day's limit isn't reached, the API isn't rejecting it).
    pub fn draft_ahead_round(&self) -> RoundOutcome {
        let held_back = self.held_back("draft-ahead").is_some();
        let mut breaker = self.breaker.lock().unwrap_or_else(PoisonError::into_inner);
        self.drafting
            .round(&mut breaker, held_back, &*self.candidates, &*self.events)
            .map(|()| None)
            .map_err(|f| f.class)
    }

    /// The morning's brief, for someone who turned it on in Settings. Nothing happens otherwise.
    pub fn brief_ahead_round(&self) -> RoundOutcome {
        if self.turned_on("morning_brief")? {
            self.ahead.write_brief_ahead().map_err(|failure| class(&failure))?;
        }
        Ok(None)
    }

    /// The week's review on Monday morning, for someone who turned it on in Settings. Nothing happens otherwise.
    pub fn review_ahead_round(&self) -> RoundOutcome {
        if self.turned_on("weekly_review")? {
            self.ahead.write_review_ahead().map_err(|failure| class(&failure))?;
        }
        Ok(None)
    }

    /// Tidy on its own, for someone who turned it on: it reads what the AI said, so not with AI off or held back.
    pub fn tidy_round(&self) -> RoundOutcome {
        if self.turned_on("auto_tidy")? && self.calls.engine() != "none" && self.held_back("auto-tidy").is_none() {
            self.tidying.round().map_err(|failure| class(&failure))?;
        }
        Ok(None)
    }

    /// The Assistant's part of the forget round: the batches the Python app sent, a month after they ended.
    pub fn forget_batches_round(&self) -> RoundOutcome {
        mm_assistant::batches::forget_finished(&*self.batches, &*self.clock).map(|_| None).map_err(|f| f.class)
    }
}
