//! What the Assistant's background work needs of the world beyond the use cases' ports (`application.ports`' `Answers`
//! and `Mail`, as triage and drafting ahead read them): the mail triage hasn't summarized, where what it says is kept,
//! and the mail a reply may be drafted ahead for.

use std::fmt;

use mm_kernel::errors::Fault;

use crate::ports::Message;

/// A message triage hasn't summarized yet, as its prompt reads it.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Pending {
    pub pk: i64,
    pub message_key: Option<i64>,
    pub from_name: Option<String>,
    pub from_addr: Option<String>,
    pub subject: Option<String>,
    pub date_iso: Option<String>,
    pub snippet: Option<String>,
    pub ts: Option<i64>,
}

/// What came of triage asking about a message (`kernel.assessment.Outcome`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Outcome {
    /// A reply with a usable summary.
    Assessed,
    /// A reply without one: what stands in is stored, so the message doesn't go round for ever.
    Unusable,
    /// The model declined, and likewise.
    Refused,
}

impl Outcome {
    pub fn name(self) -> &'static str {
        match self {
            Outcome::Assessed => "assessed",
            Outcome::Unusable => "unusable",
            Outcome::Refused => "refused",
        }
    }
}

/// What the AI said of one message, and how it came to say it (`kernel.assessment.Assessment`). `model` is the label
/// shown beside the summary, or `parse-error` for what stood in; `served_by` the model that served the reply.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Assessment {
    pub summary: String,
    /// 1 to 5.
    pub importance: i64,
    pub category: String,
    pub action: String,
    pub model: String,
    pub served_by: Option<String>,
    pub prompt_version: Option<u32>,
    pub outcome: Option<Outcome>,
}

/// Where triage finds what it hasn't summarized and keeps what it says (`Answers`' `needs_summary`, `batch_open` and
/// `save_summary`).
pub trait Triage: Send + Sync + fmt::Debug {
    /// Where the batch that is out cuts off, when one is and says so: while it is out, what came before waits for the
    /// next batch rather than going at full price.
    fn batch_cutoff(&self) -> Result<Option<i64>, Fault>;
    /// Mail that isn't junk with no summary yet, newest first, one copy of each message, none on its way out, none a
    /// batch holds and none with no key; with `newer_than`, only what came after that moment.
    fn needs_summary(&self, limit: i64, newer_than: Option<i64>) -> Result<Vec<Pending>, Fault>;
    /// Keep what the AI said of the message the copy `pk` is of, under its key, as of `when`.
    fn save_summary(&self, pk: i64, said: &Assessment, when: &str) -> Result<(), Fault>;
}

/// The mail drafting ahead may pick from (`Mail.draft_candidates`).
pub trait DraftCandidates: Send + Sync + fmt::Debug {
    /// Reply-worthy Inbox mail since `since_ts`, unanswered, not junk, snoozed, muted or on its way out, each thread by
    /// its latest message, the most important first.
    fn draft_candidates(&self, since_ts: i64) -> Result<Vec<Message>, Fault>;
}

/// The Claude API batches the Python app sent (`ai_batches`), as the forget round lets them go.
pub trait FinishedBatches: Send + Sync + fmt::Debug {
    /// Let go of the batches done that ended before `ended_before` (seconds since the epoch), their items with them:
    /// how many went.
    fn forget_finished(&self, ended_before: i64) -> Result<usize, Fault>;
}
