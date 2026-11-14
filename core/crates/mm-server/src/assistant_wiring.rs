//! The Assistant's wiring (`mailmind.bootstrap.assistant`): the language model the process talks to, and with which
//! key, the cost governor and the gateway every call goes through, and every AI use case.
//!
//! The key is the config file's `anthropic_api_key` (a Keychain item, or a variable it names), and only then
//! ANTHROPIC_API_KEY. A config key that won't resolve is no key, never a quiet switch to the environment's. Both are
//! read at each call, so a new key takes effect at once, and the model is made again only when the key changes.

use std::sync::{Arc, Mutex};

use mm_accounts::credential::{CredentialRef, Style};
use mm_accounts::ports::ConfigFile;
use mm_assistant::calls::Calls;
use mm_assistant::gateway::{Budget, Gateway};
use mm_assistant::governor::CostGovernor;
use mm_assistant::ports::{Engine, InForce, LanguageModel};
use mm_assistant::status::Status;
use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Keychain, Outside, Secret};
use mm_kernel::zone::Zone;
use mm_store::Database;

use crate::environment::Environment;
use crate::wiring::AiConfig;

const CONFIG: &str = "mailmind.config.json";
const ENVIRONMENT: &str = "ANTHROPIC_API_KEY";

/// The Claude API for the key in force, made once for each key.
pub(crate) struct ApiEngine {
    file: Arc<dyn ConfigFile>,
    keychain: Arc<dyn Keychain>,
    outside: Arc<dyn Outside>,
    environment: Environment,
    made: Mutex<Option<(Secret, Arc<dyn LanguageModel>)>>,
}

impl std::fmt::Debug for ApiEngine {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("ApiEngine(..)")
    }
}

impl ApiEngine {
    /// The key the config file names, in any credential style but a plain one or the Bridge's: None when it names
    /// none, and an empty key when it names one that can't be had.
    fn config_key(&self) -> Result<Option<Secret>, Fault> {
        let raw = self.file.read()?;
        let Some(named) = raw.get("anthropic_api_key").filter(|c| mm_accounts::account::truthy(c)) else {
            return Ok(None);
        };
        let credential = CredentialRef::parse(Some(named));
        let found = match credential.style {
            Some(Style::Keychain) => match credential.item().filter(|i| !mm_kernel::text::strip(i).is_empty()) {
                Some(item) => self.keychain.find(item).ok().flatten(),
                None => None,
            },
            Some(Style::Env) => named
                .get("env")
                .and_then(serde_json::Value::as_str)
                .and_then(|name| self.environment.credential(name))
                .filter(|v| !v.expose().is_empty()),
            // A key the file holds itself is refused, and a 1Password read isn't on the Rust server yet.
            _ => None,
        };
        let key = found.map(|k| mm_kernel::text::strip(k.expose()).to_owned()).unwrap_or_default();
        Ok(Some(Secret::new(key)))
    }

    /// The API key and where it came from, the key empty when there is none. The config file wins, so a MailMind-only
    /// key isn't shadowed by one the shell exports for other tools.
    fn key(&self) -> (Secret, &'static str) {
        match self.config_key() {
            Err(_) => (Secret::new(String::new()), CONFIG),
            Ok(Some(key)) => (key, CONFIG),
            Ok(None) => (self.environment.anthropic_key().unwrap_or_else(|| Secret::new(String::new())), ENVIRONMENT),
        }
    }
}

impl Engine for ApiEngine {
    fn in_force(&self) -> InForce {
        let (key, key_from) = self.key();
        if key.expose().is_empty() {
            return InForce::Off { key_from };
        }
        let mut made = self.made.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        if let Some((for_key, model)) = made.as_ref()
            && for_key.expose() == key.expose()
        {
            return InForce::On { model: model.clone(), key_from };
        }
        let base_url = self.environment.anthropic_base_url();
        let model: Arc<dyn LanguageModel> = Arc::new(mm_claude::ClaudeApi::new(
            Secret::new(key.expose().to_owned()),
            base_url.as_deref(),
            self.outside.clone(),
        ));
        *made = Some((key, model.clone()));
        InForce::On { model, key_from }
    }
}

/// What the Assistant is made from.
pub(crate) struct Parts {
    pub(crate) database: Arc<Database>,
    pub(crate) config: Arc<dyn ConfigFile>,
    pub(crate) keychain: Arc<dyn Keychain>,
    pub(crate) outside: Arc<dyn Outside>,
    pub(crate) clock: Arc<dyn Clock>,
    pub(crate) zone: Arc<dyn Zone>,
}

/// Every AI call, and how AI stands.
#[derive(Debug)]
pub struct Assistant {
    pub calls: Arc<Calls>,
    pub status: Arc<Status>,
    pub governor: Arc<CostGovernor>,
}

/// The API routes a hidden tab fetches on its own, which say nothing of someone being here (`shared.UI_QUIET`).
const UI_QUIET: [&str; 9] = [
    "/api/events",
    "/api/folders",
    "/api/outbox",
    "/api/pending",
    "/api/stats",
    "/api/upcoming",
    "/api/ai/draft-ahead",
    "/api/debug/health",
    "/api/places",
];

impl Assistant {
    pub(crate) fn assemble(parts: Parts, environment: &Environment) -> Assistant {
        let Parts { database, config, keychain, outside, clock, zone } = parts;
        let engine = Arc::new(ApiEngine {
            file: config.clone(),
            keychain,
            outside,
            environment: environment.clone(),
            made: Mutex::new(None),
        });
        let settings = Arc::new(AiConfig { file: config, environment_key: environment.anthropic_key().is_some() });
        let budget = Arc::new(Budget { ledger: database, settings, clock: clock.clone(), zone });
        let governor = Arc::new(CostGovernor::new(budget.clone(), clock.clone()));
        let gateway = Arc::new(Gateway::new(engine, budget, governor.clone()));
        let label = environment.model().unwrap_or_else(|| mm_assistant::models::DEFAULT.to_owned());
        let status = Arc::new(Status::new(gateway.clone(), clock, label));
        let calls = Arc::new(Calls::new(gateway, status.clone(), environment.model(), environment.no_schema()));
        Assistant { calls, status, governor }
    }

    /// Someone is here, for the cost governor's presence gate on drafting ahead: any request to the API that got past
    /// the perimeter, but the ones a hidden tab makes on its own (`shared.touched`).
    pub fn touched(&self, path: &str) {
        if path.starts_with("/api/") && !UI_QUIET.contains(&path) {
            self.governor.saw_someone();
        }
    }
}

/// How AI stands, as the Assistant reports it, for the digest.
#[derive(Debug)]
pub(crate) struct SharedStatus(pub(crate) Arc<Status>);

impl mm_mailbox::ports::AiStatus for SharedStatus {
    fn report(&self) -> Result<serde_json::Value, Fault> {
        self.0.report()
    }
}
