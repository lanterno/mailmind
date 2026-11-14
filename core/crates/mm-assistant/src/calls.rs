//! Every AI call in the app goes through `call_claude_json` or `call_claude` here (`application.calls`), which ask
//! the Claude API through the gateway, each call's outcome noted for the status. With no API key AI is off: those
//! calls are `AiOff` before anything is sent. The cost governor may hold a call back instead.

use std::sync::Arc;

use serde_json::{Map, Value};

use crate::gateway::Gateway;
use crate::models;
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

    /// "api" when an API key resolves, else "none": AI is off.
    pub fn engine(&self) -> &'static str {
        if self.gateway.on() { "api" } else { "none" }
    }

    /// The model name stored beside each AI result and shown under it.
    pub fn model_label(&self) -> String {
        self.model.clone().unwrap_or_else(|| models::DEFAULT.to_owned())
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
