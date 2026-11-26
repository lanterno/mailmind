//! The Assistant's wiring (`mailmind.bootstrap.assistant`): the language model the process talks to, and with which
//! key, the cost governor and the gateway every call goes through, and every AI use case.
//!
//! The engine in force is read from what a person chose (#49): AI on or off, its provider, model and URL, which are
//! preferences in mailmind.config.json, and the key kept for that provider. Claude, the default, goes through
//! mm-claude; every other provider through mm-openai's Chat Completions. AI off, turned off or with no key, URL or
//! model to ask, nothing is sent. MAILMIND_MODEL is the Claude model: it wins over the model a person chose while
//! Claude is the provider, and is ignored for any other. ANTHROPIC_BASE_URL stands in for Claude's own URL where none
//! was chosen.
//!
//! A provider's key is the config file's `anthropic_api_key`, `openai_api_key` and the like (a Keychain item, a
//! 1Password reference, or a variable it names), and for Claude only then ANTHROPIC_API_KEY. A config key that won't
//! resolve is no key, never a quiet switch to the environment's. The file is read at each call, so a change in
//! Settings takes effect at once; a 1Password read is kept for the process as an account's password is, and a Keychain
//! read for each version of the item the file names, forgotten as Settings keeps the key again or takes it out, as the
//! Python app keeps them. The language model is made again only when what it was made for changes: the provider, its
//! URL or its key.

use std::sync::{Arc, Mutex};

use mm_accounts::credential::{CredentialRef, Style, api_key_who};
use mm_accounts::keychain_reads::KeychainReads;
use mm_accounts::one_password::OpReads;
use mm_accounts::ports::ConfigFile;
use mm_assistant::ask::Asker;
use mm_assistant::asking::Asking;
use mm_assistant::briefs::Briefs;
use mm_assistant::bulk::BulkCommands;
use mm_assistant::calls::Calls;
use mm_assistant::catalog::Catalog;
use mm_assistant::context::Context;
use mm_assistant::extract::Extractor;
use mm_assistant::gateway::{Budget, Gateway};
use mm_assistant::governor::CostGovernor;
use mm_assistant::local_servers::Finder;
use mm_assistant::ports::{
    AccountList, Around, ConversationBy, Engine, InForce, LanguageModel, LocalServers, Mail, Message, ModelError,
    ModelLists, PlanSearch, Reached, Said, Stamp,
};
use mm_assistant::prepared_replies::PreparedReplies;
use mm_assistant::providers::{self, default_base_url, default_model, speaks_anthropic};
use mm_assistant::rationale::Rationales;
use mm_assistant::reply_draft::ReplyDrafts;
use mm_assistant::reviews::Reviews;
use mm_assistant::screening::Screening;
use mm_assistant::sender_digest::SenderDigest;
use mm_assistant::status::Status;
use mm_assistant::thread_digest::ThreadDigest;
use mm_calendar::agenda::Agenda;
use mm_discovery::query::QuerySpec;
use mm_kernel::ai_provider::AiProvider;
use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Outside, Secret};
use mm_kernel::zone::Zone;
use mm_mailbox::fetching::{Fetching, FetchingParts};
use mm_mailbox::folders::Folders;
use mm_mailbox::gates::Gates;
use mm_mailbox::ports::BodyFetch;
use mm_mailbox::session::MailServers;
use mm_store::Database;
use mm_store::discovery::DiscoveryStore;
use serde_json::{Value, json};

use crate::environment::Environment;
use crate::wiring::AiConfig;

const CONFIG: &str = providers::CONFIG;
const ENVIRONMENT: &str = "ANTHROPIC_API_KEY";

/// What a language model was made for: the provider, the URL it is reached at, its key, and the model in force. A
/// new one is a new language model, so a breaker a refused key opened on the last is closed by another choice of
/// model too, as by AI turned off and on again (which lets the last one go).
struct MadeFor {
    provider: AiProvider,
    base_url: String,
    key: Secret,
    model: String,
}

impl MadeFor {
    fn same(&self, other: &MadeFor) -> bool {
        self.provider == other.provider
            && self.base_url == other.base_url
            && self.key.expose() == other.key.expose()
            && self.model == other.model
    }
}

/// The language model of the provider and key in force, made once for each provider, URL, key and model, and again
/// after AI was off; and the models a provider lists.
pub(crate) struct ApiEngine {
    file: Arc<dyn ConfigFile>,
    keychain: Arc<KeychainReads>,
    op: Arc<OpReads>,
    outside: Arc<dyn Outside>,
    environment: Environment,
    made: Mutex<Option<(MadeFor, Arc<dyn LanguageModel>)>>,
}

impl std::fmt::Debug for ApiEngine {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("ApiEngine(..)")
    }
}

impl ApiEngine {
    /// The key the config file names for `provider`, in any credential style but a plain one or the Bridge's: None
    /// when it names none, and an empty key when it names one that can't be had.
    fn config_key(&self, provider: AiProvider) -> Result<Option<Secret>, Fault> {
        let raw = self.file.read()?;
        let Some(named) = raw.get(api_key_who(provider)).filter(|c| mm_accounts::account::truthy(c)) else {
            return Ok(None);
        };
        let credential = CredentialRef::parse(Some(named));
        let found = match credential.style {
            // Read once for each version the file names, as the Python app reads it.
            Some(Style::Keychain) => match credential.item().filter(|i| !mm_kernel::text::strip(i).is_empty()) {
                Some(item) => self.keychain.read(item, credential.version()).ok().flatten(),
                None => None,
            },
            Some(Style::Env) => named
                .get("env")
                .and_then(serde_json::Value::as_str)
                .and_then(|name| self.environment.credential(name))
                .filter(|v| !v.expose().is_empty()),
            Some(Style::Op) => credential.op_refs().and_then(|refs| self.op.read(refs, false).ok()),
            // A key the file holds itself is refused.
            _ => None,
        };
        let key = found.map(|k| mm_kernel::text::strip(k.expose()).to_owned()).unwrap_or_default();
        Ok(Some(Secret::new(key)))
    }

    /// `provider`'s API key and where it was looked for, the key empty when there is none. The config file wins, so a
    /// MailMind-only key isn't shadowed by one the shell exports for other tools; only Claude's has the environment
    /// after it.
    fn key(&self, provider: AiProvider) -> (Secret, &'static str) {
        let none = || Secret::new(String::new());
        match self.config_key(provider) {
            Err(_) => (none(), CONFIG),
            Ok(Some(key)) => (key, CONFIG),
            Ok(None) if provider == AiProvider::Anthropic => {
                (self.environment.anthropic_key().unwrap_or_else(none), ENVIRONMENT)
            }
            Ok(None) => (none(), ""),
        }
    }

    /// The preferences as they stand; their defaults when the file can't be read.
    fn preferences(&self) -> (serde_json::Map<String, Value>, AiProvider) {
        let raw = self.file.read().unwrap_or_default();
        let key_in_environment = self.environment.anthropic_key().is_some();
        (mm_accounts::preferences::read(&raw, key_in_environment), mm_accounts::preferences::provider(&raw))
    }

    /// Where `provider` is reached as chosen: the URL a person chose for it, else for Claude ANTHROPIC_BASE_URL's,
    /// else "" for its own.
    fn chosen_url(&self, provider: AiProvider, chosen: &str) -> String {
        if !chosen.is_empty() || provider != AiProvider::Anthropic {
            return chosen.to_owned();
        }
        self.environment.anthropic_base_url().unwrap_or_default()
    }

    /// The language model for `made_for`, made again only when it changed.
    fn model_for(&self, made_for: MadeFor) -> Arc<dyn LanguageModel> {
        let mut made = self.made.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        if let Some((was_for, model)) = made.as_ref()
            && was_for.same(&made_for)
        {
            return model.clone();
        }
        let key = Secret::new(made_for.key.expose().to_owned());
        // A model on this Mac may load on its first use, so each attempt waits longer for it.
        let waits = providers::attempt_limit(made_for.provider, &made_for.base_url);
        let (provider, url, outside) = (made_for.provider, made_for.base_url.as_str(), self.outside.clone());
        let model: Arc<dyn LanguageModel> = if speaks_anthropic(provider) {
            Arc::new(mm_claude::ClaudeApi::new(key, Some(url), outside).with_limits(waits, mm_claude::RETRIES))
        } else {
            Arc::new(mm_openai::ChatApi::new(provider, url, Some(key), outside).with_limits(waits, mm_openai::RETRIES))
        };
        *made = Some((made_for, model.clone()));
        model
    }
}

impl Engine for ApiEngine {
    fn in_force(&self) -> InForce {
        self.chosen().0
    }

    /// The provider's small model, MAILMIND_MODEL's for Claude where it names one, or the one chosen for a model on
    /// this Mac or a compatible service: read from the preferences, with no key looked up.
    fn background_model(&self) -> String {
        let (chosen, provider) = self.preferences();
        let model = chosen.get(mm_accounts::preferences::AI_MODEL).and_then(Value::as_str).unwrap_or_default();
        providers::background_model(provider, model, self.every(provider).as_deref())
    }
}

impl ApiEngine {
    /// The AI in force now, and whether a key was found for it.
    fn chosen(&self) -> (InForce, bool) {
        let (chosen, provider) = self.preferences();
        let said = |name: &str| chosen.get(name).and_then(Value::as_str).unwrap_or_default().to_owned();
        let enabled = chosen.get(mm_accounts::preferences::AI_ENABLED).and_then(Value::as_bool).unwrap_or(false);
        let model = self.every(provider).unwrap_or_else(|| said(mm_accounts::preferences::AI_MODEL));
        let model = if model.is_empty() { default_model(provider).to_owned() } else { model };
        let base_url = self.chosen_url(provider, &said(mm_accounts::preferences::AI_BASE_URL));
        let base_url = if base_url.is_empty() { default_base_url(provider).to_owned() } else { base_url };
        let local = providers::local(provider, &base_url);
        let has_private_mail = self.file.read().map_or(true, |config| {
            config.get("accounts").and_then(Value::as_array).is_some_and(|accounts| {
                accounts
                    .iter()
                    .any(|account| matches!(account.get("type").and_then(Value::as_str), Some("hodhod" | "gentlemail")))
            })
        });
        let enabled = enabled && (local || !has_private_mail);
        // Turned off, nothing is looked up: not even the Keychain.
        let (key, key_from) = if enabled { self.key(provider) } else { (Secret::new(String::new()), "") };
        let keyed = !key.expose().is_empty();
        let off = providers::off(enabled, provider, keyed, &base_url, &model);
        let lm = match off {
            Some(why) => {
                // Off, the last language model is let go: on again, a new one is made.
                *self.made.lock().unwrap_or_else(std::sync::PoisonError::into_inner) = None;
                Err(why)
            }
            None => Ok(self.model_for(MadeFor { provider, base_url, key, model: model.clone() })),
        };
        (InForce { provider, model, key_from, local, lm }, keyed)
    }

    /// The model every feature asks, background summaries too, in place of the one chosen: MAILMIND_MODEL is the Claude
    /// model, and wins only while Claude is the provider.
    fn every(&self, provider: AiProvider) -> Option<String> {
        if speaks_anthropic(provider) { self.environment.model() } else { None }
    }

    /// The startup log's line for the AI in force: never its URL, nor its key.
    fn describe(&self) -> String {
        let (force, keyed) = self.chosen();
        providers::startup_line(force.provider, &force.model, force.lm.err(), force.key_from, keyed)
    }
}

impl ModelLists for ApiEngine {
    fn key(&self, provider: AiProvider) -> Option<Secret> {
        Some(self.key(provider).0).filter(|key| !key.expose().is_empty())
    }

    fn base_url(&self, provider: AiProvider) -> String {
        let (chosen, chose) = self.preferences();
        let url = chosen.get(mm_accounts::preferences::AI_BASE_URL).and_then(Value::as_str).unwrap_or_default();
        self.chosen_url(provider, if chose == provider { url } else { "" })
    }

    fn list(&self, provider: AiProvider, base_url: &str, key: Option<&Secret>) -> Result<Vec<String>, ModelError> {
        let key = key.map(|k| Secret::new(k.expose().to_owned()));
        if speaks_anthropic(provider) {
            let key = key.unwrap_or_else(|| Secret::new(String::new()));
            mm_claude::ClaudeApi::new(key, Some(base_url), self.outside.clone()).list_models()
        } else {
            mm_openai::ChatApi::new(provider, base_url, key, self.outside.clone()).list_models()
        }
    }
}

/// What the Assistant is made from.
pub(crate) struct Parts {
    pub(crate) database: Arc<Database>,
    pub(crate) accounts: Arc<dyn AccountList>,
    /// What reads a body that hasn't come down from its server, on its account's gate.
    pub(crate) servers: Arc<dyn MailServers>,
    pub(crate) gates: Arc<dyn Gates>,
    /// What forgets a folder that read finds gone from its server, as opening one of its messages does.
    pub(crate) folders: Arc<Folders>,
    pub(crate) config: Arc<dyn ConfigFile>,
    /// What the Keychain gave this process, which the Directory forgets as it keeps a key again or takes one out.
    pub(crate) keychain: Arc<KeychainReads>,
    /// What 1Password gave this process, which the Directory shares.
    pub(crate) op: Arc<OpReads>,
    pub(crate) outside: Arc<dyn Outside>,
    pub(crate) clock: Arc<dyn Clock>,
    pub(crate) zone: Arc<dyn Zone>,
    /// The model servers on this Mac the finder asks (`Seams::local_ai`).
    pub(crate) local_ai: Arc<dyn LocalServers>,
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
    /// What one sender has been writing about.
    pub sender_digest: Arc<SenderDigest>,
    /// What AI cost each local day, by feature (`calls.usage`).
    pub usage: Arc<mm_assistant::usage::UsageReport>,
    /// The models a provider offers, for a person choosing one in Settings.
    pub catalog: Arc<Catalog>,
    /// The model servers running on this Mac, for a person setting up a model on this Mac.
    pub finder: Arc<Finder>,
    /// The AI in force, for the startup log's line.
    engine: Arc<ApiEngine>,
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
    /// The startup log's line for the AI in force (`describe_engine`): the provider, the model and where its key came
    /// from, or why AI is off.
    pub fn describe_engine(&self) -> String {
        self.engine.describe()
    }

    pub(crate) fn assemble(parts: Parts, environment: &Environment) -> Assistant {
        let Parts { database, accounts, servers, gates, folders, config, keychain, op, outside, clock, zone, local_ai } =
            parts;
        let around = Arc::new(Surroundings::over(&database, config.clone(), &outside, &clock, &zone));
        let engine = Arc::new(ApiEngine {
            file: config.clone(),
            keychain,
            op,
            outside,
            environment: environment.clone(),
            made: Mutex::new(None),
        });
        let settings = Arc::new(AiConfig { file: config, environment_key: environment.anthropic_key().is_some() });
        let budget = Arc::new(Budget { ledger: database.clone(), settings, clock: clock.clone(), zone: zone.clone() });
        let usage = Arc::new(mm_assistant::usage::UsageReport::new(database.clone(), clock.clone(), zone.clone()));
        let governor = Arc::new(CostGovernor::new(budget.clone(), clock.clone()));
        let catalog = Arc::new(Catalog::new(engine.clone()));
        let finder = Arc::new(Finder::new(local_ai));
        let gateway = Arc::new(Gateway::new(engine.clone(), budget, governor.clone()));
        let status = Arc::new(Status::new(gateway.clone(), clock.clone()).with_backlog(database.clone()));
        let calls = Arc::new(Calls::new(gateway, status.clone(), environment.no_schema()));
        let fetch = Arc::new(
            Fetching::new(FetchingParts {
                stored: database.clone(),
                kept: database.clone(),
                servers,
                gates,
                clock: clock.clone(),
            })
            .forgetting_gone(folders),
        );
        let mail = Arc::new(AssistantMail { database: database.clone(), fetch });
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
        let sender_digest = Arc::new(SenderDigest::new(calls.clone(), database.clone()));
        let looks_human = Box::new(|local: &str| mm_curation::classifier::looks_human(Some(local)));
        let drafting = Arc::new(PreparedReplies::new(database, context, replies, looks_human, clock));
        Assistant {
            calls,
            status,
            governor,
            digest,
            extractor,
            drafting,
            asker,
            asking,
            bulk,
            rationales,
            screening,
            sender_digest,
            usage,
            catalog,
            finder,
            engine,
        }
    }

    /// Someone is here, for the cost governor's presence gate on drafting ahead: any request to the API that got past
    /// the perimeter, but the ones a hidden tab makes on its own (`shared.touched`).
    pub fn touched(&self, path: &str) {
        if path.starts_with("/api/") && !UI_QUIET.contains(&path) {
            self.governor.saw_someone();
        }
    }
}

/// What the Assistant reads of the mail: the store's rows, and a body that hasn't come down, through the Mailbox's
/// body fetch (Python's `Mail.fetch_body`, the Mailbox's `Ingest.fetch_body`): on the account's gate at interactive
/// priority, and kept, so the next request reads it from the store.
#[derive(Debug)]
pub(crate) struct AssistantMail {
    database: Arc<Database>,
    fetch: Arc<dyn BodyFetch>,
}

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
        let raw = self.fetch.fetch(pk)?.unwrap_or_default();
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
                   "when": mm_kernel::relative::rel_time(r.date_iso.as_deref(), now, &*self.zone)})
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

/// What a review reads beyond the mail: the calendar's invites, as Today & next reads them (`calendar.agenda`), and the
/// `weekly_review` preference.
#[derive(Debug)]
pub(crate) struct Surroundings {
    config: Arc<dyn ConfigFile>,
    agenda: Agenda,
}

/// The days of mail Today & next reads invites from (`upcoming`'s `days`).
const AGENDA_DAYS: i64 = 60;

impl Surroundings {
    /// Over the invites stored mail holds, a TZID read in the system's zone database.
    fn over(
        database: &Arc<Database>,
        config: Arc<dyn ConfigFile>,
        outside: &Arc<dyn Outside>,
        clock: &Arc<dyn Clock>,
        zone: &Arc<dyn Zone>,
    ) -> Surroundings {
        let zones = Arc::new(mm_store::calendar::SystemZones::new(outside.clone()));
        let roles = Arc::new(crate::calendar_wiring::NamedRoles);
        Surroundings { config, agenda: Agenda::new(database.clone(), roles, zones, clock.clone(), zone.clone()) }
    }
}

impl Around for Surroundings {
    fn events_from(&self, at: i64, limit: usize) -> Result<Vec<Value>, Fault> {
        self.agenda.soonest(at, limit, AGENDA_DAYS)
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
