//! The Assistant's wiring (`mailmind.bootstrap.assistant`): the language model the process talks to, and with which
//! key, the cost governor and the gateway every call goes through, and every AI use case.
//!
//! The key is the config file's `anthropic_api_key` (a Keychain item, or a variable it names), and only then
//! ANTHROPIC_API_KEY. A config key that won't resolve is no key, never a quiet switch to the environment's. Both are
//! read at each call, so a new key takes effect at once, and the model is made again only when the key changes.

use std::sync::{Arc, Mutex};

use mm_accounts::credential::{CredentialRef, Style};
use mm_accounts::ports::ConfigFile;
use mm_assistant::ask::Asker;
use mm_assistant::asking::Asking;
use mm_assistant::briefs::Briefs;
use mm_assistant::bulk::BulkCommands;
use mm_assistant::calls::Calls;
use mm_assistant::context::Context;
use mm_assistant::extract::Extractor;
use mm_assistant::gateway::{Budget, Gateway};
use mm_assistant::governor::CostGovernor;
use mm_assistant::ports::{
    AccountList, Around, ConversationBy, Engine, InForce, LanguageModel, Mail, Message, PlanSearch, Reached, Said,
    Stamp,
};
use mm_assistant::prepared_replies::PreparedReplies;
use mm_assistant::rationale::Rationales;
use mm_assistant::reply_draft::ReplyDrafts;
use mm_assistant::reviews::Reviews;
use mm_assistant::screening::Screening;
use mm_assistant::status::Status;
use mm_assistant::thread_digest::ThreadDigest;
use mm_discovery::query::QuerySpec;
use mm_kernel::errors::{Fault, NOT_YET};
use mm_kernel::seams::{Clock, Keychain, Outside, Secret};
use mm_kernel::zone::Zone;
use mm_mailbox::gates::{Gates, Priority, Wait, on_gate};
use mm_mailbox::session::MailServers;
use mm_store::Database;
use mm_store::discovery::DiscoveryStore;
use serde_json::{Value, json};

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
    pub(crate) accounts: Arc<dyn AccountList>,
    /// What reads a body that hasn't come down from its server, on its account's gate.
    pub(crate) servers: Arc<dyn MailServers>,
    pub(crate) gates: Arc<dyn Gates>,
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
    /// A thread's summary, on a person's click.
    pub digest: Arc<ThreadDigest>,
    /// What a message proposes for the calendar and the to-do list.
    pub extractor: Arc<Extractor>,
    /// Replies drafted on demand, and ahead.
    pub drafting: Arc<PreparedReplies>,
    /// A question about the person's own mail.
    pub asker: Arc<Asker>,
    /// The "Today" card, "since you last looked" and the Weekly Review.
    pub asking: Arc<Asking>,
    /// A command in words, planned and previewed; the plans a preview froze.
    pub bulk: Arc<BulkCommands>,
    /// Why each sender a cleanup names is worth it.
    pub rationales: Arc<Rationales>,
    /// What the AI would say of the senders waiting in the Screener.
    pub screening: Arc<Screening>,
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
        let Parts { database, accounts, servers, gates, config, keychain, outside, clock, zone } = parts;
        let around = Arc::new(Surroundings { config: config.clone() });
        let engine = Arc::new(ApiEngine {
            file: config.clone(),
            keychain,
            outside,
            environment: environment.clone(),
            made: Mutex::new(None),
        });
        let settings = Arc::new(AiConfig { file: config, environment_key: environment.anthropic_key().is_some() });
        let budget = Arc::new(Budget { ledger: database.clone(), settings, clock: clock.clone(), zone: zone.clone() });
        let governor = Arc::new(CostGovernor::new(budget.clone(), clock.clone()));
        let gateway = Arc::new(Gateway::new(engine, budget, governor.clone()));
        let label = environment.model().unwrap_or_else(|| mm_assistant::models::DEFAULT.to_owned());
        let status = Arc::new(Status::new(gateway.clone(), clock.clone(), label));
        let calls = Arc::new(Calls::new(gateway, status.clone(), environment.model(), environment.no_schema()));
        let mail = Arc::new(AssistantMail { database: database.clone(), servers, gates });
        let context = Context::new(mail, accounts.clone());
        let index = Arc::new(DiscoveryStore::new(database.clone(), clock.clone()));
        let asker = Arc::new(Asker::new(calls.clone(), index.clone(), context.clone(), clock.clone()));
        let planning = Arc::new(Planning { store: index, clock: clock.clone(), zone: zone.clone() });
        let draw: mm_assistant::bulk::Draw =
            Arc::new(|| crate::perimeter::new_token().map_err(|_| Fault::new("Random")));
        let bulk = Arc::new(BulkCommands::new(calls.clone(), planning, clock.clone(), zone.clone(), draw));
        let digest = Arc::new(ThreadDigest::new(calls.clone(), database.clone(), context.clone(), clock.clone()));
        let extractor = Arc::new(Extractor::new(calls.clone(), context.clone(), clock.clone(), zone.clone()));
        let replies = Arc::new(ReplyDrafts::new(calls.clone(), context.clone()));
        let briefs =
            Arc::new(Briefs::new(calls.clone(), database.clone(), database.clone(), clock.clone(), zone.clone()));
        let reviews = Arc::new(Reviews::new(
            calls.clone(),
            database.clone(),
            database.clone(),
            around.clone(),
            clock.clone(),
            zone.clone(),
        ));
        let asking = Arc::new(Asking::new(briefs, reviews, accounts, around, clock.clone()));
        let rationales = Arc::new(Rationales::new(calls.clone(), database.clone(), clock.clone()));
        let screening = Arc::new(Screening::new(calls.clone(), database.clone()));
        let looks_human = Box::new(|local: &str| mm_curation::classifier::looks_human(Some(local)));
        let drafting = Arc::new(PreparedReplies::new(database, context, replies, looks_human, clock));
        Assistant { calls, status, governor, digest, extractor, drafting, asker, asking, bulk, rationales, screening }
    }

    /// Someone is here, for the cost governor's presence gate on drafting ahead: any request to the API that got past
    /// the perimeter, but the ones a hidden tab makes on its own (`shared.touched`).
    pub fn touched(&self, path: &str) {
        if path.starts_with("/api/") && !UI_QUIET.contains(&path) {
            self.governor.saw_someone();
        }
    }
}

/// What the Assistant reads of the mail: the store's rows, and a body that hasn't come down, read from its server.
///
/// Python's `Mail.fetch_body` is the Mailbox's `Ingest.fetch_body`, which keeps what it read. Until the Rust server's
/// Mailbox fetches and keeps a body (area G), the Assistant reads one here, read-only on the account's gate at
/// interactive priority, as that fetch does, and keeps nothing: the next request reads it again.
#[derive(Debug)]
pub(crate) struct AssistantMail {
    database: Arc<Database>,
    servers: Arc<dyn MailServers>,
    gates: Arc<dyn Gates>,
}

/// How long a body's read waits on each answer.
const FETCH_SECONDS: u64 = 30;

impl Mail for AssistantMail {
    fn message(&self, pk: i64) -> Result<Option<Message>, Fault> {
        self.database.message(pk)
    }

    fn conversation(&self, account: &str, by: ConversationBy, value: &str) -> Result<Vec<Said>, Fault> {
        self.database.conversation(account, by, value)
    }

    fn to_reply_to(&self, pk: i64) -> Result<Vec<Said>, Fault> {
        self.database.to_reply_to(pk)
    }

    fn fetch_text(&self, pk: i64) -> Result<Option<String>, Fault> {
        let Some((account, folder, uid)) = mm_store::assistant::copy_at(&self.database, pk)? else { return Ok(None) };
        let (Some(folder), Some(uid)) = (folder, uid.and_then(|u| u32::try_from(u).ok())) else { return Ok(None) };
        let read = on_gate(&*self.gates, &account, Priority::Interactive, Wait::Ever, || {
            let mut session = self.servers.open(&account, FETCH_SECONDS).ok()?;
            let raw = session.select(&folder, true).ok().and_then(|_| session.body(uid).ok().flatten());
            session.logout();
            raw
        });
        let raw = read.map_err(|_| Fault::new("Busy"))?.unwrap_or_default();
        Ok(mm_content::mime::parse(&raw).text.map(std::borrow::Cow::into_owned))
    }

    fn thread_stamps(&self, account: &str, thread_id: &str) -> Result<Vec<Stamp>, Fault> {
        self.database.thread_stamps(account, thread_id)
    }

    fn message_stamp(&self, pk: i64) -> Result<Option<Stamp>, Fault> {
        self.database.message_stamp(pk)
    }

    fn thread_anchor(&self, account: &str, thread_id: &str) -> Result<Option<i64>, Fault> {
        self.database.thread_anchor(account, thread_id)
    }
}

/// What a bulk command asks of Discovery: its filter read by the search's own grammar, a date in it in the person's
/// zone, and run as the search runs it.
#[derive(Debug)]
pub(crate) struct Planning {
    store: Arc<DiscoveryStore>,
    clock: Arc<dyn Clock>,
    zone: Arc<dyn Zone>,
}

impl Planning {
    fn spec(&self, filter: &str) -> QuerySpec {
        QuerySpec::parse(filter, |wall| mm_discovery::search::placed(&*self.zone, wall))
    }
}

/// Text that says something: Python's `or` passes over an empty one.
fn said(text: &Option<String>) -> Option<&str> {
    text.as_deref().filter(|t| !t.is_empty())
}

impl PlanSearch for Planning {
    fn narrows(&self, filter: &str) -> (bool, bool) {
        let spec = self.spec(filter);
        (spec.narrows(), spec.names_folder())
    }

    fn reach(&self, filter: &str, cap: i64, apart: &[&str], sample: usize) -> Result<Option<Reached>, Fault> {
        let roles = crate::bound::NamedRoles;
        let Some(reach) = self.store.reach(&self.spec(filter), cap, apart, &roles)? else { return Ok(None) };
        let now = self.clock.now();
        let messages =
            reach.rows.iter().map(|r| (r.message_key.unwrap_or_default(), r.folder.clone().unwrap_or_default()));
        let shown = reach.rows.iter().take(sample).map(|r| {
            json!({"from": said(&r.from_name).or(said(&r.from_addr)).unwrap_or_default(),
                   "subject": said(&r.subject).unwrap_or(mm_discovery::search::NO_SUBJECT),
                   "when": mm_kernel::relative::rel_time(r.date_iso.as_deref(), now)})
        });
        Ok(Some(Reached {
            count: reach.count,
            messages: messages.collect(),
            sample: shown.collect(),
            matched: reach.matched,
        }))
    }

    fn sweeps_whole_mailbox(&self, matched: i64) -> Result<bool, Fault> {
        Ok(mm_discovery::guard::sweeps_whole_mailbox(matched, self.store.total()?))
    }
}

/// What a review reads beyond the mail: the calendar's invites, and the `weekly_review` preference.
///
/// The calendar's agenda (`mm_calendar::agenda::Agenda::soonest(at, limit, 60)`) isn't on this branch: until it is wired
/// here a review says no meetings, as it does when the calendar can't be read.
#[derive(Debug)]
pub(crate) struct Surroundings {
    config: Arc<dyn ConfigFile>,
}

impl Around for Surroundings {
    fn events_from(&self, _at: i64, _limit: usize) -> Result<Vec<Value>, Fault> {
        Err(NOT_YET)
    }

    fn weekly_review(&self) -> Result<bool, Fault> {
        let raw = self.config.read()?;
        Ok(mm_accounts::preferences::said(&raw, "weekly_review").and_then(Value::as_bool).unwrap_or(false))
    }
}

/// The accounts a request may name.
impl AccountList for crate::wiring::KnownAccounts {
    fn emails(&self) -> Result<Vec<String>, Fault> {
        Ok(self.0.accounts()?.into_iter().map(|a| a.email).collect())
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
