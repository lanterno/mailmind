//! Every AI call in the app goes through `call_claude_json` or `call_claude` here (`application.calls`), which ask
//! the AI in force (Claude, or the provider a person chose) through the gateway, each call's outcome noted for the
//! status. With AI off (turned off, or no key, URL or model) those calls are `AiOff` before anything is sent. The cost
//! governor may hold a call back instead.

use std::sync::Arc;

use serde_json::{Map, Value};

use crate::gateway::Gateway;
use crate::ports::Reply;
use crate::refusals::CallError;
use crate::schema::object_in;
use crate::status::Status;

#[derive(Debug)]
pub struct Calls {
    gateway: Arc<Gateway>,
    status: Arc<Status>,
    /// MAILMIND_MODEL's, when it names one.
    model: Option<String>,
    /// MAILMIND_NO_SCHEMA: free-form text rather than structured output.
    no_schema: bool,
}

impl Calls {
    pub fn new(gateway: Arc<Gateway>, status: Arc<Status>, model: Option<String>, no_schema: bool) -> Calls {
        Calls { gateway, status, model: model.filter(|m| !m.is_empty()), no_schema }
    }

    pub fn gateway(&self) -> &Arc<Gateway> {
        &self.gateway
    }

    /// "api" while AI is on, else "none": turned off, or with no key, URL or model.
    pub fn engine(&self) -> &'static str {
        if self.gateway.on() { "api" } else { "none" }
    }

    /// The model name stored beside each AI result and shown under it: MAILMIND_MODEL's, else the one in force.
    pub fn model_label(&self) -> String {
        self.model.clone().unwrap_or_else(|| self.gateway.model())
    }

    /// Plain text (a thread's digest). `feature` names the call in the usage ledger.
    pub fn call_claude(&self, prompt: &str, feature: &str) -> Result<String, CallError> {
        self.status.noting(|| self.gateway.ask(prompt, None, feature, self.model.as_deref(), None).map(|r| r.text))
    }

    /// The whole reply to what `call_claude_json` asks, for a caller that keeps which model served it.
    pub fn reply_to(
        &self,
        prompt: &str,
        schema: &Value,
        effort: Option<&str>,
        feature: &str,
    ) -> Result<Reply, CallError> {
        let schema = (!self.no_schema).then_some(schema);
        self.status.noting(|| self.gateway.ask(prompt, schema, feature, self.model.as_deref(), effort))
    }

    /// Each (custom id, text) as one request of one batch to `model` under `schema` (none with MAILMIND_NO_SCHEMA), at
    /// `effort`, sent once: the batch's id. `model` is the one the caller's row names, read once, so the batch asks
    /// the model it is booked and labelled under even when another is chosen meanwhile. Its outcome is noted for the
    /// status, as a call's is.
    pub fn submit_batch(
        &self,
        requests: &[(String, String)],
        schema: &Value,
        effort: Option<&str>,
        feature: &str,
        model: &str,
    ) -> Result<String, CallError> {
        let schema = (!self.no_schema).then_some(schema);
        self.status.noting(|| self.gateway.submit_batch(requests, schema, feature, Some(model), effort))
    }

    /// A JSON object matching `schema`: the one structured output gives, else what a loose reading of the text
    /// finds. It fails only when no reply came; a reply that can't be read is an empty object.
    pub fn call_claude_json(
        &self,
        prompt: &str,
        schema: &Value,
        effort: Option<&str>,
        feature: &str,
    ) -> Result<Map<String, Value>, CallError> {
        Ok(object_in(&self.reply_to(prompt, schema, effort, feature)?.text))
    }
}
