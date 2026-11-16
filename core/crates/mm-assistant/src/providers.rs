//! The AI providers MailMind can ask, as the Assistant reaches them (`mailmind.assistant.domain.providers`, #49): how
//! each is spoken to and where by default, the model it asks unless a person picks one, why AI is off, and which of
//! the models a provider lists a person may pick.
//!
//! Claude is the default, asked through Anthropic's own API. Every other provider is asked through the one adapter
//! that speaks OpenAI's Chat Completions, given a base URL, a key (or none) and a model.

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
        AiProvider::Ollama | AiProvider::Compatible => "",
    }
}

/// Where a provider's API is unless a person gives another URL: "" where they must.
pub fn default_base_url(provider: AiProvider) -> &'static str {
    match provider {
        AiProvider::Anthropic => "https://api.anthropic.com",
        AiProvider::OpenAi => "https://api.openai.com/v1",
        AiProvider::Gemini => "https://generativelanguage.googleapis.com/v1beta/openai",
        AiProvider::Ollama => "http://127.0.0.1:11434/v1",
        AiProvider::Compatible => "",
    }
}

/// Whether a provider is asked through Anthropic's own API rather than OpenAI's Chat Completions.
pub fn speaks_anthropic(provider: AiProvider) -> bool {
    provider == AiProvider::Anthropic
}

/// Whether a provider runs on this machine, so a model the price table doesn't know costs nothing: Ollama, or any
/// provider at a loopback address.
pub fn local(provider: AiProvider, base_url: &str) -> bool {
    provider == AiProvider::Ollama || ai_provider::on_this_machine(base_url)
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

/// What a model's id holds when it can't chat: OpenAI's and Gemini's lists name these beside the ones that can.
const NOT_CHAT: [&str; 11] = [
    "embedding",
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
/// and for OpenAI and Gemini only the ones that can chat.
pub fn chat_models(provider: AiProvider, ids: impl IntoIterator<Item = String>) -> Vec<String> {
    let filtered = matches!(provider, AiProvider::OpenAi | AiProvider::Gemini);
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
