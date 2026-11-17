//! The AI providers MailMind can ask, as the Assistant reaches them (`mailmind.assistant.domain.providers`, #49): how
//! each is spoken to and where by default, the model it asks unless a person picks one, its small model, why AI is
//! off, and which of the models a provider lists a person may pick.
//!
//! Background summaries run on every new message, so they ask the provider's small model, its cheapest; everything a
//! person asks for goes to the model chosen.
//!
//! Claude is the default, asked through Anthropic's own API. Every other provider is asked through the one adapter
//! that speaks OpenAI's Chat Completions, given a base URL, a key (or none) and a model.

use std::time::Duration;

use mm_kernel::ai_provider::{self, AiProvider};

use crate::models;

/// Where a key was looked for, for every provider but Claude, whose key may come from ANTHROPIC_API_KEY after it.
pub const CONFIG: &str = "mailmind.config.json";

/// What a batch asked of any provider but Claude answers: only Claude's Message Batches API takes one.
pub const NO_BATCHES: &str = "Batches need Claude as the AI provider.";

/// The model a provider is asked unless a person picks one: "" where they must.
pub fn default_model(provider: AiProvider) -> &'static str {
    match provider {
        AiProvider::Anthropic => models::DEFAULT,
        AiProvider::OpenAi => "gpt-6.1-sol",
        AiProvider::Gemini => "gemini-4-hydrogen",
        AiProvider::Local | AiProvider::Compatible => "",
    }
}

/// Whose account a call to `provider` is billed to, as "the ... account has no credit left" names it.
pub fn billed_by(provider: AiProvider) -> &'static str {
    match provider {
        AiProvider::Anthropic => "Anthropic",
        AiProvider::OpenAi => "OpenAI",
        AiProvider::Gemini => "Gemini",
        AiProvider::Local => "model server",
        AiProvider::Compatible => "AI service",
    }
}

/// What the status says of a call refused for want of credit or quota on `account`: the account to top up, not a wait.
pub fn no_credit(account: &str) -> String {
    format!("the {account} account has no credit left")
}

/// The model background summaries ask of a provider: "" for a model on this Mac and a compatible service, which ask
/// the one chosen.
pub fn small_model(provider: AiProvider) -> &'static str {
    match provider {
        AiProvider::Anthropic => models::SMALL,
        AiProvider::OpenAi => "gpt-6-luna",
        AiProvider::Gemini => "gemini-3.5-flash-lite",
        AiProvider::Local | AiProvider::Compatible => "",
    }
}

/// The model background summaries ask: the one every feature asks where one is named (`every`, MAILMIND_MODEL's for
/// Claude), else the provider's small model, else the one `chosen` (a model on this Mac, a compatible service), else
/// the provider's own.
pub fn background_model(provider: AiProvider, chosen: &str, every: Option<&str>) -> String {
    [every.unwrap_or_default(), small_model(provider), chosen, default_model(provider)]
        .into_iter()
        .find(|model| !model.is_empty())
        .unwrap_or_default()
        .to_owned()
}

/// Where a provider's API is unless a person gives another URL: "" where they must.
pub fn default_base_url(provider: AiProvider) -> &'static str {
    match provider {
        AiProvider::Anthropic => "https://api.anthropic.com",
        AiProvider::OpenAi => "https://api.openai.com/v1",
        AiProvider::Gemini => "https://generativelanguage.googleapis.com/v1beta/openai",
        // Ollama's, the commonest model server on a Mac.
        AiProvider::Local => "http://127.0.0.1:11434/v1",
        AiProvider::Compatible => "",
    }
}

/// Whether a provider is asked through Anthropic's own API rather than OpenAI's Chat Completions.
pub fn speaks_anthropic(provider: AiProvider) -> bool {
    provider == AiProvider::Anthropic
}

/// Whether a provider runs on this machine, so a model the price table doesn't know costs nothing: a model on this
/// Mac, or any provider at a loopback address a base URL may be (`ai_provider::base_url`). A URL that breaks those
/// rules, ANTHROPIC_BASE_URL's among them, reaches nothing, and is not on this machine.
pub fn local(provider: AiProvider, base_url: &str) -> bool {
    provider == AiProvider::Local
        || ai_provider::base_url(base_url).is_some_and(|url| ai_provider::on_this_machine(&url))
}

/// Whether the model a server on this Mac is asked for runs in a cloud: Ollama serves its cloud models at the same
/// loopback address as the rest, and names them with a `-cloud` or `:cloud` ending (`gpt-oss:120b-cloud`), so mail
/// asked of one leaves this Mac. Only a model on this Mac, or a compatible service on loopback (`local`), can be one.
pub fn cloud_model(provider: AiProvider, local: bool, model: &str) -> bool {
    let name = model.trim().to_ascii_lowercase();
    matches!(provider, AiProvider::Local | AiProvider::Compatible)
        && local
        && (name.ends_with("-cloud") || name.ends_with(":cloud"))
}

/// How long one attempt at a call waits for its answer.
pub const ATTEMPT: Duration = Duration::from_secs(120);
/// The same for a model on this Mac, which may load on its first use.
pub const LOCAL_ATTEMPT: Duration = Duration::from_secs(300);

/// How long one attempt at a call to `provider` at `base_url` waits: `LOCAL_ATTEMPT` for a model on this Mac, or any
/// provider whose URL is on this Mac, and `ATTEMPT` for any other.
pub fn attempt_limit(provider: AiProvider, base_url: &str) -> Duration {
    if local(provider, base_url) { LOCAL_ATTEMPT } else { ATTEMPT }
}

/// Why AI is off: turned off in Settings, or with no key, URL or model to ask. Nothing is sent while it is.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Off {
    TurnedOff,
    NoKey,
    NoUrl,
    NoModel,
}

impl Off {
    /// What the status calls it.
    pub fn id(self) -> &'static str {
        match self {
            Off::TurnedOff => "turned-off",
            Off::NoKey => "no-key",
            Off::NoUrl => "no-url",
            Off::NoModel => "no-model",
        }
    }

    /// Why, in words, which never hold a secret.
    pub fn words(self) -> &'static str {
        match self {
            Off::TurnedOff => "AI is off: it is turned off in Settings",
            Off::NoKey => "AI is off: there is no API key",
            Off::NoUrl => "AI is off: the service has no URL",
            Off::NoModel => "AI is off: no model is chosen",
        }
    }
}

/// Why AI is off for what was chosen, in the order a person can do something about it, or None while it is on:
/// turned off; a provider that needs a key with none; a service with no URL; one with no model. `base_url` and
/// `model` are the ones in force, the provider's own where none was chosen.
pub fn off(enabled: bool, provider: AiProvider, has_key: bool, base_url: &str, model: &str) -> Option<Off> {
    if !enabled {
        Some(Off::TurnedOff)
    } else if provider.needs_key() && !has_key {
        Some(Off::NoKey)
    } else if base_url.is_empty() {
        Some(Off::NoUrl)
    } else if model.is_empty() {
        Some(Off::NoModel)
    } else {
        None
    }
}

/// The startup log's line for the AI in force (`describe_engine`): the provider, the model and where its key came
/// from (`keyed` when one was found), or why AI is off. Never the service's URL, which is the person's.
pub fn startup_line(provider: AiProvider, model: &str, off: Option<Off>, key_from: &str, keyed: bool) -> String {
    let model = if model.is_empty() { "no model" } else { model };
    format!("{} · {model} · {}", provider.label(), note(provider, off, key_from, keyed))
}

/// Where the key came from, or why AI is off, and how to turn it on.
fn note(provider: AiProvider, off: Option<Off>, key_from: &str, keyed: bool) -> String {
    let flag = match provider {
        AiProvider::Anthropic => String::new(),
        other => format!(" --provider {}", other.id()),
    };
    match off {
        Some(Off::TurnedOff) => {
            "AI mode is off: turn it on in Settings, or with `uv run python -m mailmind ai on`".to_owned()
        }
        Some(Off::NoKey) if key_from == CONFIG => {
            format!("no key, so AI is off: the {}_api_key in mailmind.config.json can't be read", provider.id())
        }
        Some(Off::NoKey) if provider == AiProvider::Anthropic => {
            "no key, so AI is off: keep one with `uv run python -m mailmind ai key`, or set ANTHROPIC_API_KEY"
                .to_owned()
        }
        Some(Off::NoKey) => {
            format!("no key, so AI is off: keep one in Settings, or with `uv run python -m mailmind ai key{flag}`")
        }
        Some(Off::NoUrl) => "no URL, so AI is off: give the service's in Settings".to_owned(),
        Some(Off::NoModel) => "no model, so AI is off: choose one in Settings".to_owned(),
        None if keyed => format!("key from {key_from}"),
        None => "no key".to_owned(),
    }
}

/// What a model's id holds when it can't chat: a provider's list names these beside the ones that can, and a model
/// server on this Mac its embedding models (`nomic-embed-text`).
const NOT_CHAT: [&str; 12] = [
    "embedding",
    "embed",
    "tts",
    "whisper",
    "dall-e",
    "image",
    "audio",
    "realtime",
    "transcribe",
    "moderation",
    "search",
    "live",
];

/// The models a provider's list names, as a person picks from them: sorted, once each, without Gemini's `models/`,
/// and for every provider but Claude, whose list holds no other kind, only the ones that can chat.
pub fn chat_models(provider: AiProvider, ids: impl IntoIterator<Item = String>) -> Vec<String> {
    let filtered = provider != AiProvider::Anthropic;
    let mut names: Vec<String> = ids
        .into_iter()
        .map(|id| id.strip_prefix("models/").map(str::to_owned).unwrap_or(id))
        .filter(|name| !name.is_empty())
        .filter(|name| !filtered || !NOT_CHAT.iter().any(|word| name.contains(word)))
        .collect();
    names.sort();
    names.dedup();
    names
}
