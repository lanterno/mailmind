//! The network: OpenAI's Chat Completions (`assistant.adapters.openai_chat`, #49), as the Assistant's
//! `LanguageModel` for every provider but Claude, on mm-net: OpenAI, Gemini through Google's OpenAI-compatible
//! endpoint, a model server on this Mac (Ollama, LM Studio, llama.cpp, vLLM, Jan, GPT4All), and any other service that
//! speaks it, each given its base URL, its key (or none) and its model. What goes on the wire and how a reply reads is
//! `wire`'s.
//!
//! It asks `POST {base}/chat/completions`, with `Authorization: Bearer <key>` when there is a key. A request goes over
//! HTTPS, its certificate verified on the roots the system trusts; plain HTTP goes only to this machine, so a key never
//! goes in the clear to anything off it. The host is asked of `Outside` before a connection is made. An attempt's time
//! limit is a deadline for all of it, connecting, sending and reading the whole answer, of 32 MiB at most. A call is
//! sent again, twice, after a status a retry can help (408, 409, 429, 5xx) or a lost connection, waiting what
//! `retry-after` says or else a little longer each time. A model on this Mac and a compatible service may not take
//! structured output: a 400 that names `response_format` is asked once more without it, the schema stated after the
//! prompt instead, and the reply read as free-form JSON. Neither is sent an output ceiling; OpenAI and Gemini are,
//! and a 400 whose words name it is asked once more without it. A rejected key (a 401 or a 403, or Gemini's 400
//! saying the key isn't valid, read as a 401) is never asked again without either, and has an error that holds none
//! of what the service said, which can quote part of the key back; no error ever holds the key, and a key that isn't
//! printable ASCII without a space (one with a space or a line break in it, or any character outside ASCII) is
//! refused so too, never sent. It takes no batch: only Claude's API does.

pub mod wire;

use std::sync::Arc;
use std::time::Duration;

use mm_assistant::ports::{LanguageModel, ModelError, Prompt, RETRIED, Reply};
use mm_kernel::ai_provider::{self, AiProvider};
use mm_kernel::seams::{Outside, Secret};
use mm_net::Lost;
use mm_net::http::{self, Endpoint, backoff};
use wire::Without;

/// Seconds per attempt.
pub const TIMEOUT: Duration = Duration::from_secs(120);
/// Attempts after the first.
pub const RETRIES: u32 = 2;

/// One provider's Chat Completions, reached at one base URL with one key, or none.
pub struct ChatApi {
    provider: AiProvider,
    key: Option<Secret>,
    endpoint: Option<Endpoint>,
    outside: Arc<dyn Outside>,
    timeout: Duration,
    retries: u32,
}

impl std::fmt::Debug for ChatApi {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ChatApi")
            .field("provider", &self.provider)
            .field("endpoint", &self.endpoint)
            .finish_non_exhaustive()
    }
}

/// How an attempt went: a response, or a connection that didn't go or was lost.
enum Attempt {
    Answered(http::Response),
    Lost(Lost),
}

impl ChatApi {
    /// `provider`'s API at `base_url`, called with `key` (None, or an empty one, for none). A URL that names no HTTP
    /// endpoint makes every call fail as unreachable.
    pub fn new(provider: AiProvider, base_url: &str, key: Option<Secret>, outside: Arc<dyn Outside>) -> ChatApi {
        let key = key.filter(|k| !k.expose().is_empty());
        ChatApi { provider, key, endpoint: Endpoint::parse(base_url), outside, timeout: TIMEOUT, retries: RETRIES }
    }

    /// The same, each attempt held to `timeout` all told, and a call sent again up to `retries` times.
    pub fn with_limits(mut self, timeout: Duration, retries: u32) -> ChatApi {
        self.timeout = timeout;
        self.retries = retries;
        self
    }

    /// The ids `GET {base}/models` names, asked once: a person is waiting. Fails as a call does.
    pub fn list_models(&self) -> Result<Vec<String>, ModelError> {
        let response = self.call("GET", "/models", None, 0)?;
        Ok(wire::model_ids(&parsed(&response).unwrap_or(serde_json::Value::Null)))
    }

    /// The same, but an answer that isn't a list of models fails as one that can't be read: what tells a model server
    /// from anything else listening at its address.
    pub fn readable_models(&self) -> Result<Vec<String>, ModelError> {
        let response = self.call("GET", "/models", None, 0)?;
        parsed(&response)
            .as_ref()
            .and_then(wire::model_list)
            .ok_or_else(|| ModelError::new(format!("{} answered what can't be read", self.provider.id()), None))
    }

    /// One attempt, which the time limit holds all told: connecting, sending and reading the whole answer.
    fn attempt(&self, endpoint: &Endpoint, request: &[u8]) -> Attempt {
        if self.outside.reach(&endpoint.host).is_err() {
            return Attempt::Lost(Lost("Refused"));
        }
        match endpoint.send(request, self.timeout) {
            Ok(response) => Attempt::Answered(response),
            Err(lost) => Attempt::Lost(lost),
        }
    }

    /// A ModelError that says only that nothing answered, and why, by the class of the failure.
    fn unreachable(&self, why: &str) -> ModelError {
        ModelError::new(format!("{} unreachable: {why}", self.provider.id()), None)
    }

    /// `method` on `path` after the base, with `body` as JSON, sent again up to `retries` times after a status a retry
    /// can help or a lost connection: the response, whatever its status, or a ModelError when nothing answered.
    fn send(&self, method: &str, path: &str, body: Option<&[u8]>, retries: u32) -> Result<http::Response, ModelError> {
        let Some(endpoint) = &self.endpoint else {
            return Err(self.unreachable("no API address"));
        };
        // A key goes in the clear only to this machine, and nothing does to a host off it over plain HTTP.
        if !endpoint.tls && !endpoint.loopback() {
            return Err(self.unreachable("plain HTTP is only for this machine"));
        }
        // Every provider's key is printable ASCII without a space, so any other is refused as a 401 is, and never sent.
        if self.key.as_ref().is_some_and(|k| !ai_provider::sendable(k.expose())) {
            return Err(ModelError::new(format!("{} API 401: the key was refused", self.provider.id()), Some(401)));
        }
        let bearer = self.key.as_ref().map(|k| format!("Bearer {}", k.expose()));
        let mut headers = vec![("Accept", "application/json"), ("User-Agent", "MailMind")];
        if body.is_some() {
            headers.push(("Content-Type", "application/json"));
        }
        if let Some(bearer) = &bearer {
            headers.push(("Authorization", bearer.as_str()));
        }
        let path = format!("{}{path}", endpoint.base);
        let request = http::request(method, &path, &endpoint.host_header(), &headers, body);
        let mut retried = 0;
        loop {
            let attempt = self.attempt(endpoint, &request);
            let again = match &attempt {
                Attempt::Answered(response) => retried_status(response.status),
                Attempt::Lost(lost) => lost.0 != "Refused",
            };
            if again && retried < retries {
                let response = match &attempt {
                    Attempt::Answered(response) => Some(response),
                    Attempt::Lost(_) => None,
                };
                std::thread::sleep(backoff(response, retried));
                retried += 1;
                continue;
            }
            return match attempt {
                Attempt::Lost(Lost("Timeout")) => Err(self.unreachable("timed out")),
                Attempt::Lost(Lost("ConnectionRefused")) => Err(self.unreachable("connection refused")),
                Attempt::Lost(Lost(class)) => Err(self.unreachable(class)),
                Attempt::Answered(response) => Ok(response),
            };
        }
    }

    /// `send`, and a ModelError for an answer that isn't a 2xx: what the service said, unless it refused the key.
    fn call(&self, method: &str, path: &str, body: Option<&[u8]>, retries: u32) -> Result<http::Response, ModelError> {
        let response = self.send(method, path, body, retries)?;
        if (200..300).contains(&response.status) { Ok(response) } else { Err(self.refused(&response)) }
    }

    /// The ModelError for an answer that isn't a 2xx, with what the service said unless it refused the key, and
    /// never the key. A 400 that says the key isn't valid (Gemini's answer to a wrong key) is a refused key, as a 401
    /// is.
    fn refused(&self, response: &http::Response) -> ModelError {
        let provider = self.provider.id();
        let status = response.status;
        if matches!(status, 401 | 403) {
            return ModelError::new(format!("{provider} API {status}: the key was refused"), Some(status));
        }
        if status == 400 && wire::key_invalid(&response.body) {
            return ModelError::new(format!("{provider} API 401: the key was refused"), Some(401));
        }
        let said = wire::said(&response.body, self.key.as_ref().map(Secret::expose));
        ModelError::new(format!("{provider} API {status}: {said}"), Some(status))
    }

    /// Whether a 400 says the service doesn't take structured output: only a model on this Mac and a compatible service
    /// are asked again without it.
    fn refused_format(&self, prompt: &Prompt, response: &http::Response) -> bool {
        response.status == 400
            && prompt.schema.is_some()
            && matches!(self.provider, AiProvider::Local | AiProvider::Compatible)
            && response.body.windows(b"response_format".len()).any(|w| w == b"response_format")
    }

    /// Whether a 400 refuses the output ceiling: what the service said names `max_tokens` or `max_completion_tokens`,
    /// to a provider that is sent one (OpenAI and Gemini).
    fn refused_ceiling(&self, response: &http::Response) -> bool {
        if response.status != 400 || wire::ceiling(self.provider).is_none() {
            return false;
        }
        let said = wire::error_text(&response.body);
        said.contains("max_tokens") || said.contains("max_completion_tokens")
    }
}

/// The statuses a retry can help: a timeout, a conflict, a rate limit, and any server error.
fn retried_status(status: u16) -> bool {
    RETRIED.contains(&status) || (500..600).contains(&status)
}

/// A response's JSON body, or None when it isn't JSON.
fn parsed(response: &http::Response) -> Option<serde_json::Value> {
    serde_json::from_slice(&response.body).ok()
}

impl LanguageModel for ChatApi {
    fn complete(&self, prompt: &Prompt) -> Result<Reply, ModelError> {
        let provider = self.provider.id();
        let failed = || ModelError::new(format!("{provider} failed: the request can't be written"), None);
        let mut without = Without::default();
        let response = loop {
            let body = serde_json::to_vec(&wire::body(prompt, self.provider, without)).map_err(|_| failed())?;
            let response = self.send("POST", "/chat/completions", Some(&body), self.retries)?;
            // A key refused with a 400 is never asked again, as one refused with a 401 never is. What else a service
            // refused is left out of the next request, each at most once a call, structured output first.
            if response.status == 400 && wire::key_invalid(&response.body) {
                break response;
            } else if !without.format && self.refused_format(prompt, &response) {
                without.format = true;
            } else if !without.ceiling && self.refused_ceiling(&response) {
                without.ceiling = true;
            } else {
                break response;
            }
        };
        if !(200..300).contains(&response.status) {
            return Err(self.refused(&response));
        }
        match parsed(&response) {
            Some(serde_json::Value::Object(answer)) => Ok(wire::reply(&answer, &prompt.model)),
            _ => Err(ModelError::new(format!("{provider} answered what can't be read"), None)),
        }
    }
}
