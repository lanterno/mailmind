//! The model servers running on this Mac, for a person setting up a model on this Mac (`GET /api/ai/local`, #49):
//! each one MailMind knows of is asked for its models at its usual address, all at once and briefly, and those that
//! answer a list are offered, in the known order, with the models a person may pick. No key goes with the question
//! and nothing off this Mac is reached; it sends no mail and spends nothing, so it goes with AI mode off too. Nothing
//! found is no server, never an error.

use std::sync::Arc;

use mm_kernel::ai_provider::AiProvider;
use serde_json::{Value, json};

use crate::ports::LocalServers;
use crate::providers::chat_models;

/// The model servers MailMind knows of, by name, at their usual addresses, in the order they are offered.
pub const KNOWN: [(&str, &str); 6] = [
    ("Ollama", "http://127.0.0.1:11434/v1"),
    ("LM Studio", "http://127.0.0.1:1234/v1"),
    ("llama.cpp", "http://127.0.0.1:8080/v1"),
    ("vLLM", "http://127.0.0.1:8000/v1"),
    ("Jan", "http://127.0.0.1:1337/v1"),
    ("GPT4All", "http://127.0.0.1:4891/v1"),
];

#[derive(Debug)]
pub struct Finder {
    servers: Arc<dyn LocalServers>,
}

impl Finder {
    pub fn new(servers: Arc<dyn LocalServers>) -> Finder {
        Finder { servers }
    }

    /// `{ok, servers: [{name, url, models}]}`: each server that answered a list of models, in the order asked, with
    /// the models it lists that can chat, sorted, once each.
    pub fn find(&self) -> Value {
        let servers: Vec<Value> = self
            .servers
            .ask()
            .into_iter()
            .filter_map(|asked| {
                let models = chat_models(AiProvider::Local, asked.models?);
                Some(json!({"name": asked.name, "url": asked.url, "models": models}))
            })
            .collect();
        json!({"ok": true, "servers": servers})
    }
}
