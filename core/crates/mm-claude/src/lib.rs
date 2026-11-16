//! The network: the Claude API's Messages endpoint and its Message Batches (`assistant.adapters.anthropic_sdk`), as
//! the Assistant's `LanguageModel`, on mm-net.
//!
//! A request goes over HTTPS, its certificate verified on the roots the system trusts. Plain HTTP goes only to a
//! loopback host, which is what a stand-in for the API is: the key is never sent in the clear to anything off this
//! machine, and never to any endpoint but the one it was given for. The host is asked of `Outside` before a connection
//! is made. A call is sent again, twice, after a status worth sending again (`RETRIED`) or a lost connection, waiting
//! what `retry-after` says or else a little longer each time, except a batch's sending, which never is; any other
//! status is a `ModelError` saying it. The key never reaches a log line or an error. It also lists the models the key
//! may ask, for a person choosing one (#49).

pub mod batches;
pub mod wire;

use std::sync::Arc;
use std::time::Duration;

use mm_assistant::models::FALLBACK_BETA;
use mm_assistant::ports::{BatchResult, LanguageModel, ModelError, Prompt, RETRIED, Reply};
use mm_kernel::seams::{Outside, Secret};
use mm_net::http::backoff;
pub use mm_net::http::{self, Endpoint};
use mm_net::{Lost, Stream};

/// Where API calls go unless ANTHROPIC_BASE_URL names another.
pub const DEFAULT_URL: &str = "https://api.anthropic.com";
/// The API version every request names.
pub const VERSION: &str = "2026-10-21";
/// Seconds per attempt.
pub const TIMEOUT: Duration = Duration::from_secs(120);
/// Attempts after the first.
pub const RETRIES: u32 = 2;

/// The Claude API, reached with one key at one endpoint.
pub struct ClaudeApi {
    key: Secret,
    endpoint: Option<Endpoint>,
    outside: Arc<dyn Outside>,
    timeout: Duration,
    retries: u32,
}

impl std::fmt::Debug for ClaudeApi {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ClaudeApi").field("endpoint", &self.endpoint).finish_non_exhaustive()
    }
}

/// How an attempt went: a response, or a connection that didn't go or was lost.
enum Attempt {
    Answered(http::Response),
    Lost(Lost),
}

impl ClaudeApi {
    /// The API at `base_url` (DEFAULT_URL when None or empty), called with `key`. A URL that names no HTTP endpoint
    /// makes every call fail as unreachable.
    pub fn new(key: Secret, base_url: Option<&str>, outside: Arc<dyn Outside>) -> ClaudeApi {
        let url = base_url.filter(|u| !u.trim().is_empty()).unwrap_or(DEFAULT_URL);
        ClaudeApi { key, endpoint: Endpoint::parse(url), outside, timeout: TIMEOUT, retries: RETRIES }
    }

    /// The same, waiting `timeout` for each answer and sending a call again up to `retries` times.
    pub fn with_limits(mut self, timeout: Duration, retries: u32) -> ClaudeApi {
        self.timeout = timeout;
        self.retries = retries;
        self
    }

    fn connect(&self, endpoint: &Endpoint) -> Result<Stream, Lost> {
        self.outside.reach(&endpoint.host).map_err(|_| Lost("Refused"))?;
        endpoint.open(self.timeout)
    }

    fn attempt(&self, endpoint: &Endpoint, request: &[u8]) -> Attempt {
        let mut stream = match self.connect(endpoint) {
            Ok(stream) => stream,
            Err(lost) => return Attempt::Lost(lost),
        };
        let answered = http::exchange(&mut stream, request);
        stream.close_notify();
        match answered {
            Ok(response) => Attempt::Answered(response),
            Err(lost) => Attempt::Lost(lost),
        }
    }
}

/// What a request accepts back: JSON, or a batch's results as JSON lines.
const JSON: &str = "application/json";
const BINARY: &str = "application/binary";

/// One request to the API, as `ClaudeApi::call` sends it.
struct Call<'a> {
    method: &'a str,
    /// The path after the endpoint's base.
    path: &'a str,
    accept: &'static str,
    beta: Option<&'static str>,
    /// A JSON body, if it has one.
    body: Option<&'a [u8]>,
    /// Attempts after the first.
    retries: u32,
}

impl ClaudeApi {
    /// `call` sent with the key, and sent again up to its `retries` times after a status worth sending again or a lost
    /// connection: the response when its status says it worked, else a `ModelError` saying why there is none. The key
    /// goes only over TLS, or in the clear to this machine.
    fn call(&self, call: &Call<'_>) -> Result<http::Response, ModelError> {
        let Some(endpoint) = &self.endpoint else {
            return Err(ModelError::new("Claude API unreachable: no API address", None));
        };
        // The key goes in the clear only to this machine.
        if !endpoint.tls && !endpoint.loopback() {
            return Err(ModelError::new("Claude API unreachable: plain HTTP is only for this machine", None));
        }
        let mut headers = vec![("Accept", call.accept)];
        if call.body.is_some() {
            headers.push(("Content-Type", JSON));
        }
        headers.extend([("Anthropic-Version", VERSION), ("X-Api-Key", self.key.expose())]);
        if let Some(beta) = call.beta {
            headers.push(("Anthropic-Beta", beta));
        }
        if self.key.expose().contains(['\r', '\n']) {
            return Err(ModelError::new("Claude API failed: the key can't be sent", None));
        }
        let path = format!("{}{}", endpoint.base, call.path);
        let request = http::request(call.method, &path, &endpoint.host_header(), &headers, call.body);
        let mut retried = 0;
        loop {
            let attempt = self.attempt(endpoint, &request);
            let again = match &attempt {
                Attempt::Answered(response) => RETRIED.contains(&response.status),
                Attempt::Lost(lost) => lost.0 != "Refused",
            };
            if again && retried < call.retries {
                let response = match &attempt {
                    Attempt::Answered(response) => Some(response),
                    Attempt::Lost(_) => None,
                };
                std::thread::sleep(backoff(response, retried));
                retried += 1;
                continue;
            }
            return match attempt {
                Attempt::Lost(Lost("Timeout")) => {
                    Err(ModelError::new("Claude API unreachable: Request timed out.", None))
                }
                Attempt::Lost(_) => Err(ModelError::new("Claude API unreachable: Connection error.", None)),
                Attempt::Answered(response) if (200..300).contains(&response.status) => Ok(response),
                Attempt::Answered(response) => Err(ModelError::new(
                    format!("Claude API {}: {}", response.status, wire::error_text(&response.body)),
                    Some(response.status),
                )),
            };
        }
    }
}

/// A response's JSON body, or the SDK's validation error when it isn't JSON.
fn parsed(response: &http::Response) -> Result<serde_json::Value, ModelError> {
    serde_json::from_slice(&response.body)
        .map_err(|_| ModelError::new("Claude API failed: APIResponseValidationError", None))
}

impl LanguageModel for ClaudeApi {
    fn complete(&self, prompt: &Prompt) -> Result<Reply, ModelError> {
        let body = serde_json::to_vec(&wire::body(prompt)).map_err(|_| ModelError::new("Claude API failed", None))?;
        let beta = prompt.fallback.then_some(FALLBACK_BETA);
        let call =
            Call { method: "POST", path: "/v1/messages", accept: JSON, beta, body: Some(&body), retries: self.retries };
        Ok(wire::reply(&parsed(&self.call(&call)?)?, &prompt.model))
    }

    fn submit_batch(&self, requests: &[(String, Prompt)]) -> Result<String, ModelError> {
        self.send_batch(requests)
    }

    fn batch_ended(&self, batch_id: &str) -> Result<bool, ModelError> {
        Ok(self.retrieve(batch_id)?.get("processing_status").and_then(serde_json::Value::as_str) == Some("ended"))
    }

    fn batch_results(&self, batch_id: &str, asked: &str) -> Result<Vec<BatchResult>, ModelError> {
        self.results(batch_id, asked)
    }

    fn discard_batch(&self, batch_id: &str) -> Result<(), ModelError> {
        self.delete(batch_id)
    }
}

/// The most models the API lists at once.
const MODELS_PAGE: u32 = 1000;
/// The most pages of models read: a list longer than this is cut off, never followed forever.
const MODELS_PAGES: u32 = 20;

impl ClaudeApi {
    /// The ids of the models the key may ask, from `GET /v1/models`, every page of it (#49). Each page is asked once:
    /// a person is waiting. Fails as a call does.
    pub fn list_models(&self) -> Result<Vec<String>, ModelError> {
        let mut ids = Vec::new();
        let mut after: Option<String> = None;
        for _ in 0..MODELS_PAGES {
            let path = match &after {
                None => format!("/v1/models?limit={MODELS_PAGE}"),
                Some(last) => format!("/v1/models?limit={MODELS_PAGE}&after_id={}", query_value(last)?),
            };
            let call = Call { method: "GET", path: &path, accept: JSON, beta: None, body: None, retries: 0 };
            let page = parsed(&self.call(&call)?)?;
            let listed = page.get("data").and_then(serde_json::Value::as_array).cloned().unwrap_or_default();
            let named = listed.iter().filter_map(|m| m.get("id")?.as_str()).filter(|id| !id.is_empty());
            ids.extend(named.map(str::to_owned));
            let more = page.get("has_more").and_then(serde_json::Value::as_bool).unwrap_or(false);
            match page.get("last_id").and_then(serde_json::Value::as_str) {
                Some(last) if more && !last.is_empty() => after = Some(last.to_owned()),
                _ => break,
            }
        }
        Ok(ids)
    }
}

/// An id as a query's value: letters, digits, `-`, `_`, `.` and `:` only, so nothing the API said can change the
/// request.
fn query_value(id: &str) -> Result<&str, ModelError> {
    if id.bytes().all(|b| b.is_ascii_alphanumeric() || b"-_.:".contains(&b)) {
        Ok(id)
    } else {
        Err(ModelError::new("Claude API failed: APIResponseValidationError", None))
    }
}
