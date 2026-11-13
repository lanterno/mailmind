//! What the Assistant's use cases need of the world: the settings that turn AI on and bound what it spends, and the
//! ledger of what calls cost.

use std::fmt;

use mm_kernel::errors::Fault;
use serde_json::Value;

/// Where AI's settings are: the config file's `anthropic_api_key` and `ai_daily_limit_usd`, and the environment's
/// ANTHROPIC_API_KEY.
pub trait AiSettings: Send + Sync + fmt::Debug {
    /// Whether a key is named anywhere: the config file names one, or the environment gives one.
    fn key_named(&self) -> Result<bool, Fault>;
    /// The config file's `ai_daily_limit_usd`, as the file has it.
    fn daily_limit(&self) -> Result<Option<Value>, Fault>;
}

/// What each day's calls cost (`ai_usage`).
pub trait UsageLedger: Send + Sync + fmt::Debug {
    /// What the calls of `day` (`YYYY-MM-DD`) cost, in micro-dollars.
    fn spent(&self, day: &str) -> Result<i64, Fault>;
}
