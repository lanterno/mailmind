//! The Message Batches API (`messages.batches`), as the SDK's adapter calls it: a batch created and never sent again,
//! asked whether it has ended, its results read as JSON lines once it has, and removed.
//!
//! Every call goes where `ClaudeApi::call` sends one, with the same key, TLS and bounds. A batch's results are read
//! from that same endpoint, at the path the API serves them on, once the batch names a `results_url`: the SDK fetches
//! that URL itself, with the key, wherever it points, and this never sends the key anywhere but the endpoint it was
//! given for. A batch's id goes into the path percent-encoded, and one that would name another path is refused unsent.

use mm_assistant::ports::{BatchResult, ModelError, Prompt};
use serde_json::{Value, json};

use crate::{BINARY, Call, ClaudeApi, JSON, parsed, wire};

/// Where the batches hang from.
const BATCHES: &str = "/v1/messages/batches";

/// `id` as one segment of a path: every byte but a letter, a digit and `-._~` percent-encoded. None for an id that is
/// empty or a dot-segment, which would name another path; the SDK refuses those too.
pub fn segment(id: &str) -> Option<String> {
    if id.is_empty() || id == "." || id == ".." {
        return None;
    }
    let mut out = String::with_capacity(id.len());
    for byte in id.bytes() {
        if byte.is_ascii_alphanumeric() || b"-._~".contains(&byte) {
            out.push(char::from(byte));
        } else {
            out.push_str(&format!("%{byte:02X}"));
        }
    }
    Some(out)
}

/// The path of the batch `id`, and what comes after it.
fn path(id: &str, after: &str) -> Result<String, ModelError> {
    match segment(id) {
        Some(segment) => Ok(format!("{BATCHES}/{segment}{after}")),
        None => Err(ModelError::new("Claude API failed: no batch is named", None)),
    }
}

impl ClaudeApi {
    /// Each (custom id, prompt) as one request of one batch, sent once: the batch's id.
    pub(crate) fn send_batch(&self, requests: &[(String, Prompt)]) -> Result<String, ModelError> {
        let requests: Vec<Value> = requests.iter().map(|(id, prompt)| wire::batch_request(id, prompt)).collect();
        let body = serde_json::to_vec(&json!({ "requests": requests }))
            .map_err(|_| ModelError::new("Claude API failed", None))?;
        // No key says "the same batch", so another try could pay for it twice.
        let call = Call { method: "POST", path: BATCHES, accept: JSON, beta: None, body: Some(&body), retries: 0 };
        let made = parsed(&self.call(&call)?)?;
        match made.get("id").and_then(Value::as_str).filter(|id| !id.is_empty()) {
            Some(id) => Ok(id.to_owned()),
            None => Err(ModelError::new("Claude API failed: APIResponseValidationError", None)),
        }
    }

    /// The batch as the API describes it.
    pub(crate) fn retrieve(&self, id: &str) -> Result<Value, ModelError> {
        let path = path(id, "")?;
        let call = Call { method: "GET", path: &path, accept: JSON, beta: None, body: None, retries: self.retries };
        parsed(&self.call(&call)?)
    }

    /// What each request of a batch that has ended came to. One with no results yet fails, as the SDK's does.
    pub(crate) fn results(&self, id: &str, asked: &str) -> Result<Vec<BatchResult>, ModelError> {
        let named = self.retrieve(id)?;
        if !named.get("results_url").is_some_and(|url| url.as_str().is_some_and(|url| !url.is_empty())) {
            return Err(ModelError::new("Claude API failed: AnthropicError", None));
        }
        let path = path(id, "/results")?;
        let call = Call { method: "GET", path: &path, accept: BINARY, beta: None, body: None, retries: self.retries };
        wire::batch_results(&self.call(&call)?.body, asked)
    }

    /// Remove what the API keeps of a batch.
    pub(crate) fn delete(&self, id: &str) -> Result<(), ModelError> {
        let path = path(id, "")?;
        let call = Call { method: "DELETE", path: &path, accept: JSON, beta: None, body: None, retries: self.retries };
        self.call(&call).map(drop)
    }
}
