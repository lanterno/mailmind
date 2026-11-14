//! What the Assistant's use cases need of the world (`mailmind.assistant.application.ports`): a language model for the
//! key in force, the settings that turn AI on and bound what it spends, and the ledger of what calls cost.
//!
//! A `LanguageModel` sends a `Prompt` as one user turn and gives back the `Reply`. When no reply comes it fails with a
//! `ModelError`: the API rejected the call (`status` says how), or nothing answered (`status` is None).

use std::fmt;
use std::sync::Arc;

use mm_kernel::errors::Fault;
use serde_json::Value;

/// The statuses worth sending again: a timeout, a conflict, a rate limit, an overload, a server error. Any other 4xx
/// says the request itself won't do.
pub const RETRIED: [u16; 8] = [408, 409, 429, 500, 502, 503, 504, 529];

/// One user turn, ready to send, under its system prompt.
#[derive(Clone, Debug, PartialEq)]
pub struct Prompt {
    pub text: String,
    pub model: String,
    pub max_tokens: u32,
    /// Structured output's form of the schema (`schema::structured`).
    pub schema: Option<Value>,
    /// None: the model takes no effort setting.
    pub effort: Option<String>,
    /// Ask for the server-side refusal fallback.
    pub fallback: bool,
    pub system: Option<&'static str>,
}

/// The tokens a reply was billed for.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Usage {
    pub input_tokens: i64,
    pub output_tokens: i64,
    pub cache_write_tokens: i64,
    pub cache_read_tokens: i64,
}

/// What came back: the answer's text ("" for a refusal), the model that served it (another one, after a server-side
/// fallback), its usage and why it stopped.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Reply {
    pub text: String,
    pub model: String,
    pub usage: Usage,
    pub stop_reason: String,
}

impl Reply {
    pub fn refused(&self) -> bool {
        self.stop_reason == "refusal"
    }
}

/// No reply: the API rejected the call with `status`, or nothing answered (status None). What it says is the API's
/// own words, kept only to tell a rejected key or a rate limit from the rest (`plain`): it never prints, and never
/// holds the key.
#[derive(Clone, PartialEq, Eq)]
pub struct ModelError {
    pub status: Option<u16>,
    said: String,
}

impl fmt::Debug for ModelError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ModelError").field("status", &self.status).finish_non_exhaustive()
    }
}

impl ModelError {
    pub fn new(said: impl Into<String>, status: Option<u16>) -> ModelError {
        ModelError { status, said: said.into() }
    }

    /// A rejection that sending the same thing again won't change: a bad request, key or model, not a rate limit or
    /// an outage.
    pub fn permanent(&self) -> bool {
        self.status.is_some_and(|s| (400..500).contains(&s) && !RETRIED.contains(&s))
    }

    /// The failure in words the setup card can show (`status._plain`), never what the API said: it can quote the
    /// request back.
    pub fn plain(&self) -> String {
        let lower = self.said.to_lowercase();
        if self.said.contains(" 401") || lower.contains("authentication") || lower.contains("invalid x-api-key") {
            return "the API key was rejected".to_owned();
        }
        if self.said.contains(" 429") || lower.contains("rate") {
            return "rate-limited for now".to_owned();
        }
        if lower.contains("timed out") {
            return "it timed out".to_owned();
        }
        match self.status {
            Some(status) => format!("the API answered {status}"),
            None => "ModelError".to_owned(),
        }
    }
}

/// What became of one request in a batch: its reply, or None with `permanent` saying that sending it again won't
/// change that (a bad request, not an outage, a cancel or an expiry).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BatchResult {
    pub custom_id: String,
    pub reply: Option<Reply>,
    pub permanent: bool,
}

/// What a language model that offers no batches answers when asked for one.
const NO_BATCHES: &str = "Claude API failed: this model takes no batches";

/// The Messages API, or whatever stands in for it, and its Message Batches. A stand-in that offers no batches fails
/// every batch call, as nothing answered.
pub trait LanguageModel: Send + Sync + fmt::Debug {
    fn complete(&self, prompt: &Prompt) -> Result<Reply, ModelError>;

    /// Each (custom id, prompt) as one request of one batch: the batch's id. Never sent again: the API has no key that
    /// says "the same batch", so another try could pay for it twice.
    fn submit_batch(&self, _requests: &[(String, Prompt)]) -> Result<String, ModelError> {
        Err(ModelError::new(NO_BATCHES, None))
    }

    /// Whether the batch has ended, so its results can be read.
    fn batch_ended(&self, _batch_id: &str) -> Result<bool, ModelError> {
        Err(ModelError::new(NO_BATCHES, None))
    }

    /// What each request of a batch that has ended came to, its replies read as from a request for model `asked`.
    fn batch_results(&self, _batch_id: &str, _asked: &str) -> Result<Vec<BatchResult>, ModelError> {
        Err(ModelError::new(NO_BATCHES, None))
    }

    /// Remove what the API keeps of a batch that has ended.
    fn discard_batch(&self, _batch_id: &str) -> Result<(), ModelError> {
        Err(ModelError::new(NO_BATCHES, None))
    }
}

/// The language model for the key in force now, or that AI is off; and where the key came from, or where it was
/// looked for.
#[derive(Clone, Debug)]
pub enum InForce {
    On { model: Arc<dyn LanguageModel>, key_from: &'static str },
    Off { key_from: &'static str },
}

/// Where the key and the API are: read at each call, so a new key takes effect at once, and the model made again
/// only when the key or the API's address changes. A key that can't be had is no key.
pub trait Engine: Send + Sync + fmt::Debug {
    fn in_force(&self) -> InForce;
}

/// Where AI's settings are: the config file's `anthropic_api_key` and `ai_daily_limit_usd`, and the environment's
/// ANTHROPIC_API_KEY.
pub trait AiSettings: Send + Sync + fmt::Debug {
    /// Whether a key is named anywhere: the config file names one, or the environment gives one.
    fn key_named(&self) -> Result<bool, Fault>;
    /// The config file's `ai_daily_limit_usd`, as the file has it.
    fn daily_limit(&self) -> Result<Option<Value>, Fault>;
}

/// One call, as the usage ledger counts it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Spend {
    /// The local date it counts against, YYYY-MM-DD.
    pub day: String,
    /// What made it: triage, thread, brief, draft, ...
    pub feature: String,
    /// The model asked for.
    pub model: String,
    /// The model that served it; "" when no reply came.
    pub served: String,
    /// ok, refused, rejected (a permanent 4xx) or failed.
    pub outcome: &'static str,
    pub usage: Usage,
    /// US dollars × 1,000,000.
    pub cost_micros: i64,
    /// When it ended, in seconds since the epoch.
    pub ts: i64,
    /// More than one for the answers of a batch, counted together.
    pub calls: i64,
}

/// What each day's calls cost (`ai_usage`), and what batches still out may.
pub trait UsageLedger: Send + Sync + fmt::Debug {
    fn record(&self, spend: &Spend) -> Result<(), Fault>;
    /// What the calls of `day` (`YYYY-MM-DD`) cost, in micro-dollars.
    fn spent(&self, day: &str) -> Result<i64, Fault>;
    /// What batches still out may cost, in micro-dollars.
    fn reserved(&self) -> Result<i64, Fault>;
    /// How many messages are out in a batch.
    fn in_batches(&self) -> Result<i64, Fault>;
}

/// What one feature's calls of one day came to, as the usage ledger adds them up.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FeatureDay {
    /// The local date, YYYY-MM-DD.
    pub day: String,
    pub feature: String,
    pub calls: i64,
    /// US dollars × 1,000,000.
    pub cost_micros: i64,
}

/// What the usage ledger says of the days gone (`UsageLedger.days`).
pub trait UsageDays: Send + Sync + fmt::Debug {
    /// Each day from `since` (`YYYY-MM-DD`) on and each feature that made calls on it, the newest day first and its
    /// features in order of name.
    fn days(&self, since: &str) -> Result<Vec<FeatureDay>, Fault>;
}

/// A message of a conversation, as the model is shown it (`conversation_rows`).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Said {
    pub pk: i64,
    pub account: String,
    pub from_name: Option<String>,
    pub from_addr: Option<String>,
    pub date_iso: Option<String>,
    pub subject: Option<String>,
    pub snippet: Option<String>,
    pub body_text: Option<String>,
    pub message_id: Option<String>,
    pub body_fetched: bool,
    pub ts: Option<i64>,
}

/// A message's row as the Assistant reads it to answer about it (`get_message`): whose it is, its thread, and what
/// says whether a reply is wanted.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Message {
    pub pk: i64,
    pub account: String,
    pub thread_id: Option<String>,
    pub message_id: Option<String>,
    pub message_key: Option<i64>,
    pub from_addr: Option<String>,
    pub folder: Option<String>,
    pub is_junk: bool,
    pub answered: bool,
    /// Whether `snoozes` says it wakes later.
    pub snoozed: bool,
}

/// What a conversation's messages share: their thread, or for a message with no thread its Message-ID or its pk.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ConversationBy {
    Thread,
    MessageId,
    Pk,
}

/// A copy's Message-ID and a number beside it: its time, or whether its body is here.
pub type Stamp = (Option<String>, Option<i64>);

/// What the Assistant reads of the mail (`Mail`): never anything a request wrote into SQL.
pub trait Mail: Send + Sync + fmt::Debug {
    fn message(&self, pk: i64) -> Result<Option<Message>, Fault>;
    /// A conversation's copies, oldest first.
    fn conversation(&self, account: &str, by: ConversationBy, value: &str) -> Result<Vec<Said>, Fault>;
    /// A message with no thread as a reply is drafted from it, in a list as a conversation's are.
    fn to_reply_to(&self, pk: i64) -> Result<Vec<Said>, Fault>;
    /// A message's text from its server, at interactive priority, when none is stored: None where there is none.
    fn fetch_text(&self, pk: i64) -> Result<Option<String>, Fault>;
    /// Each copy's Message-ID and time: the thread's state.
    fn thread_stamps(&self, account: &str, thread_id: &str) -> Result<Vec<Stamp>, Fault>;
    /// A message's Message-ID and `body_fetched`: a lone message's state.
    fn message_stamp(&self, pk: i64) -> Result<Option<Stamp>, Fault>;
    /// The thread's latest message, the only one its prepared reply answers.
    fn thread_anchor(&self, account: &str, thread_id: &str) -> Result<Option<i64>, Fault>;
}

/// The accounts the app may use, by address: a request naming another is refused.
pub trait AccountList: Send + Sync + fmt::Debug {
    fn emails(&self) -> Result<Vec<String>, Fault>;
}

/// A thread's summary as `thread_summaries` keeps it, its lists as JSON text.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct StoredSummary {
    pub account: String,
    pub thread_id: String,
    pub scope: String,
    pub fingerprint: Option<String>,
    pub tldr: Option<String>,
    pub points: Option<String>,
    pub action: Option<String>,
    pub participants: Option<String>,
    pub message_count: Option<i64>,
    pub model: Option<String>,
    pub created_at: Option<String>,
}

/// A reply drafted ahead as `draft_ahead` keeps it.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct StoredDraft {
    pub fp: Option<String>,
    pub body: Option<String>,
    pub tone: Option<String>,
    pub model: Option<String>,
    pub created_ts: i64,
    pub fail_ts: i64,
    pub fail_count: i64,
}

/// A reply drafted, to keep under its key.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct NewDraft<'a> {
    pub fp: &'a str,
    pub body: &'a str,
    pub tone: Option<&'a str>,
    pub model: Option<&'a str>,
    pub src_message_id: Option<&'a str>,
    /// What made it: dwell, topk or demand.
    pub source: &'a str,
}

/// A message a question to Ask reads (`for_asking`).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct AskRow {
    pub pk: i64,
    pub message_key: Option<i64>,
    pub uid: Option<i64>,
    pub account: String,
    pub folder: Option<String>,
    pub from_name: Option<String>,
    pub from_addr: Option<String>,
    pub subject: Option<String>,
    pub snippet: Option<String>,
    pub body_text: Option<String>,
    pub date_iso: Option<String>,
    /// What the AI's triage said of it.
    pub ai_summary: Option<String>,
}

/// What a question reads (Discovery's index).
pub trait AskIndex: Send + Sync + fmt::Debug {
    /// What `fts_match` finds in `accounts`, best first, or with nothing to match the newest mail; None for a query
    /// the index rejects. No account matches nothing.
    fn for_asking(&self, fts_match: &str, accounts: &[String], limit: i64) -> Result<Option<Vec<AskRow>>, Fault>;
}

/// What a bulk command's filter reaches: how many messages, each by its key and the folder it was seen in, the few a
/// preview shows, and how many rows the filter says whatever it may reach.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Reached {
    pub count: i64,
    pub messages: Vec<(i64, String)>,
    pub sample: Vec<Value>,
    pub matched: i64,
}

/// What a bulk command asks of Discovery: its filter read by the search grammar, never trusted raw.
pub trait PlanSearch: Send + Sync + fmt::Debug {
    /// Whether `filter` picks out part of the mailbox, and whether it names a folder.
    fn narrows(&self, filter: &str) -> (bool, bool);
    /// What `filter` reaches, up to `cap`, leaving out the folders playing the roles in `apart`; None for a query the
    /// index rejects.
    fn reach(&self, filter: &str, cap: i64, apart: &[&str], sample: usize) -> Result<Option<Reached>, Fault>;
    /// Whether `matched` rows are most of the mailbox.
    fn sweeps_whole_mailbox(&self, matched: i64) -> Result<bool, Fault>;
}

/// What the Assistant keeps of its answers (`Answers`).
pub trait Answers: Send + Sync + fmt::Debug {
    fn thread_summary(&self, account: &str, thread_id: &str, scope: &str) -> Result<Option<StoredSummary>, Fault>;
    fn save_thread_summary(&self, summary: &StoredSummary) -> Result<(), Fault>;
    fn draft_ahead(&self, account: &str, ref_key: &str) -> Result<Option<StoredDraft>, Fault>;
    fn put_draft_ahead(&self, account: &str, ref_key: &str, draft: &NewDraft<'_>, now: i64) -> Result<(), Fault>;
    /// A generation failed: the failure is counted, and a body already there stays.
    fn draft_ahead_failed(&self, account: &str, ref_key: &str, now: i64) -> Result<(), Fault>;
    fn draft_ahead_touched(&self, account: &str, ref_key: &str, now: i64) -> Result<(), Fault>;
    /// Hygiene only: the old, the moot, the orphaned (`orphans`) and past the cap go.
    fn draft_ahead_gc(&self, now: i64, orphans: bool) -> Result<(), Fault>;
    /// Whether the AI's verdict on the message calls it worth a reply drafted ahead.
    fn reply_worthy(&self, pk: i64) -> Result<bool, Fault>;
}

/// A message a brief or a delta is made from, with what the AI said of it.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Glance {
    pub pk: i64,
    pub message_key: Option<i64>,
    pub folder: Option<String>,
    pub from_name: Option<String>,
    pub from_addr: Option<String>,
    pub subject: Option<String>,
    pub message_id: Option<String>,
    pub summary: Option<String>,
    pub importance: Option<i64>,
    pub category: Option<String>,
    pub action: Option<String>,
}

/// A message that still needs the person, for a review.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Needing {
    pub message_key: Option<i64>,
    pub uid: Option<i64>,
    pub folder: Option<String>,
    pub account: String,
    pub thread_id: Option<String>,
    pub from_name: Option<String>,
    pub from_addr: Option<String>,
    pub subject: Option<String>,
    pub importance: Option<i64>,
    pub action: Option<String>,
}

/// A thread the person waits on an answer in, by its latest message where it has one.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Awaiting {
    pub account: String,
    pub thread_id: String,
    pub message_key: Option<i64>,
    pub uid: Option<i64>,
    pub folder: Option<String>,
    pub first_sent_ts: Option<i64>,
    pub subject: Option<String>,
    pub recipients: Option<String>,
}

/// The week a review looks back on, as the mail, the follow-ups and the unsubscribes count it.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct WeekRows {
    pub sent: i64,
    pub received: i64,
    /// What kind of mail came in: each bucket, and how many.
    pub by_bucket: Vec<(Option<String>, i64)>,
    pub needs: Vec<Needing>,
    pub waiting: Vec<Awaiting>,
    pub reply_later: i64,
    /// None where the unsubscribes can't be counted.
    pub cleaned: Option<i64>,
    pub handled: i64,
}

/// The copy a stored answer's message has now: its folder and uid.
pub type CopyNow = Option<(Option<String>, Option<i64>)>;

/// What a brief, a delta and a review read of the stored mail. Each confines itself to `accounts`, or reads every
/// account when none is named.
pub trait Overview: Send + Sync + fmt::Debug {
    /// The mail the AI said is worth a glance, most important first.
    fn brief_rows(&self, accounts: &[String], n: i64) -> Result<Vec<Glance>, Fault>;
    /// The mail newer than `since_ts`, newest first, with what the AI said of each or its defaults.
    fn delta_rows(&self, accounts: &[String], since_ts: i64, n: i64) -> Result<Vec<Glance>, Fault>;
    /// The copy each stored message has now, among them the one in the folder it was read from; None for one that
    /// has gone.
    fn copies_now(&self, copies: &[(i64, Option<String>)]) -> Result<Vec<CopyNow>, Fault>;
    /// The week since `since_ts` (`since_iso` as text), with at most `top` of each list.
    fn week(&self, accounts: &[String], since_ts: f64, since_iso: &str, top: i64) -> Result<WeekRows, Fault>;
}

/// What a review reads beyond the mail: the calendar, and whether it is written ahead and read all week.
pub trait Around: Send + Sync + fmt::Debug {
    /// The events in the mail's invites that hadn't ended at `at` (seconds since the epoch), soonest first, at most
    /// `limit`, as Today & next lists them.
    fn events_from(&self, at: i64, limit: usize) -> Result<Vec<Value>, Fault>;
    /// The `weekly_review` preference.
    fn weekly_review(&self) -> Result<bool, Fault>;
}

/// A brief, or a delta, as it is kept: `text` is its narrative or headline, `body` its groups or items as JSON.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct StoredCard {
    pub fingerprint: String,
    pub text: String,
    pub body: String,
    pub model: Option<String>,
    pub created_at: String,
}

/// A review as it is kept, its lists as JSON.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct StoredReview {
    pub fingerprint: String,
    pub narrative: String,
    pub next: String,
    pub stats: String,
    pub needs: String,
    pub waiting: String,
    pub model: Option<String>,
    pub created_at: String,
}

/// Briefs, deltas and reviews as they are kept: each saved forgets those of its kind made before `forget_before`.
pub trait Cards: Send + Sync + fmt::Debug {
    fn brief(&self, accounts: &str, day: &str) -> Result<Option<StoredCard>, Fault>;
    fn save_brief(&self, accounts: &str, day: &str, brief: &StoredCard, forget_before: &str) -> Result<(), Fault>;
    fn delta(&self, accounts: &str, since_ts: i64, fingerprint: &str) -> Result<Option<StoredCard>, Fault>;
    fn save_delta(&self, accounts: &str, since_ts: i64, delta: &StoredCard, forget_before: &str) -> Result<(), Fault>;
    fn review(&self, accounts: &str, day: &str) -> Result<Option<StoredReview>, Fault>;
    fn save_review(&self, accounts: &str, day: &str, review: &StoredReview, forget_before: &str) -> Result<(), Fault>;
}

/// How one sender stands in the stored mail, each message counted once however many folders hold it: `addr` lower
/// case, the times in seconds since the epoch.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct SenderStats {
    pub addr: String,
    pub name: Option<String>,
    pub n: i64,
    pub unseen: Option<i64>,
    pub replied: Option<i64>,
    pub first_ts: Option<i64>,
    pub last_ts: Option<i64>,
}

/// One of a sender's newest messages, one a message however many folders hold it, with what the AI said of it.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct SenderRecent {
    pub account: String,
    pub thread_id: Option<String>,
    pub from_name: Option<String>,
    pub subject: Option<String>,
    pub date_iso: Option<String>,
    pub snippet: Option<String>,
    pub summary: Option<String>,
}

/// What the Assistant reads of the stored mail about senders, nothing on its way out counted.
pub trait Senders: Send + Sync + fmt::Debug {
    /// How each of `addrs` (lower case) that has mail stored stands, the one with the most first.
    fn sender_stats(&self, addrs: &[String]) -> Result<Vec<SenderStats>, Fault>;
    /// The newest `n` messages of each of `addrs`, one list an address, read together.
    fn senders_recent(&self, addrs: &[&str], n: i64) -> Result<Vec<Vec<SenderRecent>>, Fault>;
}
