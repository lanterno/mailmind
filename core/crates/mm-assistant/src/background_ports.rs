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

/// A batch sent to the Claude API, as `ai_batches` keeps it: by this process or the Python app, which keep the same
/// rows. `state` is `submitting` until the API has it, then `running`, `collected` once its answers are stored and
/// `done` once the API has let it go.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Batch {
    pub id: i64,
    /// The API's id for it, once it has one.
    pub remote_id: Option<String>,
    pub feature: String,
    /// The model it asked.
    pub model: String,
    pub state: String,
    pub requests: i64,
    /// What it may cost, booked against the daily limit while it is out, in micro-dollars.
    pub estimate_micros: i64,
    /// The newest message it took: while it is out, what came before waits for the next batch.
    pub cutoff_ts: i64,
    /// Seconds since the epoch.
    pub created_at: i64,
    pub polled_at: i64,
    pub ended_at: Option<i64>,
    /// The triage prompt's version its requests were made with; None from before migration 43 said.
    pub prompt_version: Option<i64>,
}

/// A batch about to be sent: what it holds, and what it may cost.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Begun<'a> {
    pub feature: &'a str,
    pub model: &'a str,
    /// The messages it holds, by key.
    pub keys: &'a [i64],
    pub estimate_micros: i64,
    pub cutoff_ts: i64,
    pub now: i64,
    pub prompt_version: u32,
}

/// What a stored answer is made from: the copy of its message there is now, if one is, and the reply.
pub type Judge<'a> = &'a dyn Fn(Option<&Pending>, &crate::ports::Reply) -> Assessment;

/// Where the batches sent to the Claude API are kept (`Answers`' `batch_*`): one out at a time, the messages each holds
/// by key, so nothing triages those meanwhile.
pub trait BatchBook: Send + Sync + fmt::Debug {
    /// The batch that is out (`submitting` or `running`), if any.
    fn open(&self) -> Result<Option<Batch>, Fault>;
    /// The batches in `state`, oldest first.
    fn in_state(&self, state: &str) -> Result<Vec<Batch>, Fault>;
    /// Record a batch about to be sent and hold its messages, committed before it is sent, which is a network call:
    /// its id, or None when another is out already.
    fn begin(&self, begun: &Begun<'_>) -> Result<Option<i64>, Fault>;
    /// The API has it, under `remote_id`.
    fn sent(&self, id: i64, remote_id: &str, now: i64) -> Result<(), Fault>;
    /// It never went, or the API no longer has it: its messages are waiting again.
    fn drop_batch(&self, id: i64) -> Result<(), Fault>;
    /// The API was asked about it at `now`.
    fn polled(&self, id: i64, now: i64) -> Result<(), Fault>;
    /// In one write: each (key, reply) of a key the batch holds kept as `judge` makes it, as of `when`, for the
    /// message's copies and any that comes later, and the batch collected at `now`, so a message it held with no
    /// answer is waiting again. A message no copy is left of keeps only an answer that was assessed. How many answers
    /// were for keys it holds.
    fn collected(
        &self,
        id: i64,
        now: i64,
        when: &str,
        answers: &[(i64, crate::ports::Reply)],
        judge: Judge<'_>,
    ) -> Result<usize, Fault>;
    /// The API has let it go.
    fn discarded(&self, id: i64) -> Result<(), Fault>;
    /// How many messages are out in a batch.
    fn waiting(&self) -> Result<i64, Fault>;
}

/// The Claude API batches sent (`ai_batches`), as the forget round lets them go.
pub trait FinishedBatches: Send + Sync + fmt::Debug {
    /// Let go of the batches done that ended before `ended_before` (seconds since the epoch), their items with them:
    /// how many went.
    fn forget_finished(&self, ended_before: i64) -> Result<usize, Fault>;
}
