//! What each Claude model takes (`mailmind.assistant.domain.models`): an effort setting, and the server-side refusal
//! fallback.

/// Every feature's model unless MAILMIND_MODEL names another.
pub const DEFAULT: &str = "claude-opus-5-5";
/// Sent explicitly: models differ in their default.
pub const EFFORT: &str = "medium";
pub const FALLBACK_BETA: &str = "server-side-fallback-2026-07-01";

/// Models that accept `fallbacks: "default"`: a classifier decline is re-run server-side on the model Anthropic
/// recommends for that category instead of coming back as a refusal. Never Haiku, which has no server-side fallback.
const FALLBACK: [&str; 4] = ["claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"];
/// Models older than `output_config.effort`, which reject it (MAILMIND_MODEL may name one).
const NO_EFFORT: [&str; 2] = ["claude-haiku-4-5", "claude-sonnet-4-5"];

pub fn takes_effort(model: &str) -> bool {
    !NO_EFFORT.iter().any(|old| model.starts_with(old))
}

pub fn takes_fallback(model: &str) -> bool {
    FALLBACK.contains(&model)
}
