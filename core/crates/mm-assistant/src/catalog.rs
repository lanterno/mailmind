//! The models a provider offers, for a person choosing one in Settings (`application.catalog`, #49): asked of the
//! provider with the key kept for it, at the URL a person chose for it while it is the provider chosen, else its own.
//! It sends no mail and spends nothing, so it goes with AI turned off too: the person is setting AI up. What can't be
//! asked is refused in words, and a provider that didn't answer is unreachable.

use std::sync::Arc;

use mm_kernel::ai_provider::AiProvider;
use mm_kernel::errors::{Kind, Problem};
use serde_json::{Value, json};

use crate::ports::ModelLists;
use crate::providers::{chat_models, default_base_url, default_model};

/// A service with no URL, chosen or its own.
pub const NO_URL: &str = "Give the service's URL first.";
/// A provider that needs a key, with none kept.
pub const NO_KEY: &str = "Keep a key for this provider first.";
/// A 401 or a 403.
pub const KEY_REFUSED: &str = "The provider refused the key.";
/// Anything else: no answer, or one that isn't a list.
pub const UNREACHABLE: &str = "Couldn't reach the provider.";

#[derive(Debug)]
pub struct Catalog {
    lists: Arc<dyn ModelLists>,
}

impl Catalog {
    pub fn new(lists: Arc<dyn ModelLists>) -> Catalog {
        Catalog { lists }
    }

    /// `{ok, models, default}`: the ids `provider` lists that a person may choose, and the one it asks unless they do
    /// (null where they must).
    pub fn models(&self, provider: AiProvider) -> Result<Value, Problem> {
        let chosen = self.lists.base_url(provider);
        let base_url = if chosen.is_empty() { default_base_url(provider).to_owned() } else { chosen };
        if base_url.is_empty() {
            return Err(Problem::refused(NO_URL));
        }
        let key = if provider.takes_key() { self.lists.key(provider) } else { None };
        let key = key.filter(|k| !k.expose().is_empty());
        if provider.needs_key() && key.is_none() {
            return Err(Problem::refused(NO_KEY));
        }
        let ids = match self.lists.list(provider, &base_url, key.as_ref()) {
            Ok(ids) => ids,
            Err(error) if matches!(error.status, Some(401 | 403)) => return Err(Problem::refused(KEY_REFUSED)),
            Err(_) => return Err(Problem::new(Kind::Unreachable, UNREACHABLE)),
        };
        let default = Some(default_model(provider)).filter(|m| !m.is_empty());
        Ok(json!({"ok": true, "models": chat_models(provider, ids), "default": default}))
    }
}
