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

/// The Messages API, or whatever stands in for it.
pub trait LanguageModel: Send + Sync + fmt::Debug {
    fn complete(&self, prompt: &Prompt) -> Result<Reply, ModelError>;
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
