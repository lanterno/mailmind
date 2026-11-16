//! The AI in force (#49): why AI is off and in which order, what each provider asks by default, which of a
//! provider's models a person may pick, what the gateway sends and counts for a provider other than Claude, what the
//! status says of it, and the models a provider lists.

use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};

use mm_assistant::calls::Calls;
use mm_assistant::catalog::{Catalog, KEY_REFUSED, NO_KEY, NO_URL, UNREACHABLE};
use mm_assistant::gateway::{Budget, Gateway};
use mm_assistant::governor::CostGovernor;
use mm_assistant::local_servers::Finder;
use mm_assistant::ports::{
    AiSettings, Engine, InForce, LanguageModel, LocalServer, LocalServers, ModelError, ModelLists, Prompt, Reply,
    Spend, Usage, UsageLedger,
};
use mm_assistant::providers::{self, Off, chat_models, default_base_url, default_model};
use mm_assistant::refusals::{CallError, NotSent};
use mm_assistant::status::Status;
use mm_kernel::ai_provider::AiProvider::{self, Anthropic, Compatible, Gemini, Local, OpenAi};
use mm_kernel::errors::{Fault, Kind, Problem};
use mm_kernel::seams::{Clock, Moment, Secret};
use mm_kernel::zone::Utc;
use serde_json::{Value, json};

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
}

// -- why AI is off -----------------------------------------------------------------

#[test]
fn ai_is_off_turned_off_first_then_for_a_key_a_url_and_a_model() {
    use providers::off;
    assert_eq!(off(false, Anthropic, true, "https://api.anthropic.com", "claude-opus-5-5"), Some(Off::TurnedOff));
    assert_eq!(off(false, Compatible, false, "", ""), Some(Off::TurnedOff), "turned off says so whatever else");
    for needs in [Anthropic, OpenAi, Gemini] {
        assert_eq!(off(true, needs, false, default_base_url(needs), default_model(needs)), Some(Off::NoKey));
        assert_eq!(off(true, needs, true, default_base_url(needs), default_model(needs)), None);
    }
    assert_eq!(off(true, Compatible, false, "", ""), Some(Off::NoUrl), "a compatible service's key is optional");
    assert_eq!(off(true, Compatible, false, "https://llm.example.test/v1", ""), Some(Off::NoModel));
    assert_eq!(off(true, Compatible, false, "https://llm.example.test/v1", "mixtral"), None);
    assert_eq!(
        off(true, Local, false, default_base_url(Local), ""),
        Some(Off::NoModel),
        "a model on this Mac needs no key"
    );
    assert_eq!(off(true, Local, false, default_base_url(Local), "llama3.2:3b"), None);
}

#[test]
fn each_reason_has_its_id_and_its_words() {
    let said: Vec<(&str, &str)> =
        [Off::TurnedOff, Off::NoKey, Off::NoUrl, Off::NoModel].iter().map(|o| (o.id(), o.words())).collect();
    assert_eq!(
        said,
        [
            ("turned-off", "AI is off: it is turned off in Settings"),
            ("no-key", "AI is off: there is no API key"),
            ("no-url", "AI is off: the service has no URL"),
            ("no-model", "AI is off: no model is chosen"),
        ]
    );
}

#[test]
fn each_provider_has_its_own_model_and_url_where_it_has_one() {
    let own: Vec<(&str, &str)> = AiProvider::ALL.iter().map(|p| (default_model(*p), default_base_url(*p))).collect();
    assert_eq!(
        own,
        [
            ("claude-opus-5-5", "https://api.anthropic.com"),
            ("gpt-6.1-sol", "https://api.openai.com/v1"),
            ("gemini-4-hydrogen", "https://generativelanguage.googleapis.com/v1beta/openai"),
            ("", "http://127.0.0.1:11434/v1"),
            ("", ""),
        ]
    );
    assert!(providers::local(Local, ""));
    assert!(providers::local(Compatible, "http://localhost:1234/v1"));
    assert!(providers::local(Anthropic, "http://127.0.0.1:8080"));
    assert!(!providers::local(Compatible, "https://llm.example.test/v1"));
    assert!(!providers::local(OpenAi, default_base_url(OpenAi)));
    // A URL on this Mac that breaks the rules a base URL is held to (ANTHROPIC_BASE_URL's among them) reaches
    // nothing, and is not on this Mac: a model the table doesn't know is priced as any other's.
    let long = format!("http://127.0.0.1:8080/{}", "v".repeat(500));
    for broken in
        ["http://localhost:0", "http://pat@127.0.0.1:8080", "http://127.0.0.1:8080/?x", " http://[::1]:8080", &long]
    {
        assert!(!providers::local(Anthropic, broken), "{broken}");
        assert!(!providers::local(Compatible, broken), "{broken}");
        assert_eq!(providers::attempt_limit(Anthropic, broken), providers::ATTEMPT, "{broken}");
    }
    assert!(providers::local(Local, "http://localhost:0"), "a model on this Mac is on it, whatever its URL");
}

#[test]
fn a_call_to_a_model_on_this_mac_waits_five_minutes_an_attempt_and_any_other_two() {
    use providers::attempt_limit;
    use std::time::Duration;
    let five = Duration::from_secs(300);
    assert_eq!(attempt_limit(Local, default_base_url(Local)), five);
    assert_eq!(attempt_limit(Local, "http://[::1]:8080/v1"), five);
    assert_eq!(attempt_limit(Compatible, "http://localhost:1234/v1"), five, "any provider whose URL is on this Mac");
    assert_eq!(attempt_limit(Anthropic, "http://127.0.0.1:8080"), five);
    let two = Duration::from_secs(120);
    for (provider, url) in [(Compatible, "https://llm.example.test/v1"), (Anthropic, default_base_url(Anthropic))] {
        assert_eq!(attempt_limit(provider, url), two, "{provider:?}");
    }
    assert_eq!(attempt_limit(OpenAi, default_base_url(OpenAi)), two);
}

#[test]
fn the_startup_line_names_the_provider_the_model_and_the_key_or_why_ai_is_off_and_how_to_turn_it_on() {
    use providers::startup_line as line;
    let config = providers::CONFIG;
    assert_eq!(
        line(Anthropic, "claude-opus-5-5", None, "ANTHROPIC_API_KEY", true),
        "Claude (Anthropic) · claude-opus-5-5 · key from ANTHROPIC_API_KEY"
    );
    assert_eq!(line(OpenAi, "gpt-6.1-sol", None, config, true), "OpenAI · gpt-6.1-sol · key from mailmind.config.json");
    assert_eq!(line(Local, "llama3.2:3b", None, "", false), "A model on this Mac · llama3.2:3b · no key");
    assert_eq!(
        line(Compatible, "mixtral", None, config, false),
        "Another OpenAI-compatible service · mixtral · no key",
        "a key named that can't be read is no key"
    );
    assert_eq!(
        line(Gemini, "gemini-4-hydrogen", Some(Off::TurnedOff), "", false),
        "Google Gemini · gemini-4-hydrogen · AI mode is off: turn it on in Settings, or with `uv run python -m \
         mailmind ai on`"
    );
    assert_eq!(
        line(Anthropic, "claude-opus-5-5", Some(Off::NoKey), "ANTHROPIC_API_KEY", false),
        "Claude (Anthropic) · claude-opus-5-5 · no key, so AI is off: keep one with `uv run python -m mailmind ai key`, \
         or set ANTHROPIC_API_KEY"
    );
    assert_eq!(
        line(OpenAi, "gpt-6.1-sol", Some(Off::NoKey), config, false),
        "OpenAI · gpt-6.1-sol · no key, so AI is off: the openai_api_key in mailmind.config.json can't be read"
    );
    assert_eq!(
        line(Gemini, "gemini-4-hydrogen", Some(Off::NoKey), "", false),
        "Google Gemini · gemini-4-hydrogen · no key, so AI is off: keep one in Settings, or with `uv run python -m \
         mailmind ai key --provider gemini`"
    );
    assert_eq!(
        line(Compatible, "mixtral", Some(Off::NoUrl), "", false),
        "Another OpenAI-compatible service · mixtral · no URL, so AI is off: give the service's in Settings"
    );
    assert_eq!(
        line(Local, "", Some(Off::NoModel), "", false),
        "A model on this Mac · no model · no model, so AI is off: choose one in Settings"
    );
}

#[test]
fn a_person_picks_from_the_models_that_can_chat_sorted_once_each() {
    let listed = |ids: &[&str]| ids.iter().map(|s| (*s).to_owned()).collect::<Vec<_>>();
    let openai = listed(&[
        "gpt-6.1-sol",
        "text-embedding-3-large",
        "gpt-4o-mini-tts",
        "whisper-1",
        "dall-e-3",
        "gpt-image-1",
        "gpt-4o-audio-preview",
        "gpt-realtime",
        "gpt-4o-transcribe",
        "omni-moderation-latest",
        "gpt-4o-search-preview",
        "gpt-6-luna",
        "gpt-6.1-sol",
    ]);
    assert_eq!(chat_models(OpenAi, openai), ["gpt-6-luna", "gpt-6.1-sol"]);
    let gemini =
        listed(&["models/gemini-4-hydrogen", "models/gemini-embedding-001", "models/gemini-live-2.5", "models/"]);
    assert_eq!(chat_models(Gemini, gemini), ["gemini-4-hydrogen"]);
    // A model on this Mac and a compatible service list their embedding and speech models too.
    let local = listed(&["qwen3:8b", "nomic-embed-text:latest", "llama3.2:3b", "mxbai-embed-large", "qwen3:8b"]);
    assert_eq!(chat_models(Local, local), ["llama3.2:3b", "qwen3:8b"]);
    let compatible = listed(&["mixtral-8x7b", "text-embedding-ada-002", "whisper-large-v3", "bge-m3-embed"]);
    assert_eq!(chat_models(Compatible, compatible), ["mixtral-8x7b"]);
    // Claude's list names no other kind, and is kept whole.
    let claude = listed(&["claude-sonnet-5-5", "claude-opus-5-5", "claude-live-fictional", "claude-sonnet-5-5"]);
    assert_eq!(chat_models(Anthropic, claude), ["claude-live-fictional", "claude-opus-5-5", "claude-sonnet-5-5"]);
}

// -- the gateway and the status, with a provider other than Claude ---------------------------------------------

#[derive(Debug)]
struct Frozen;

impl Clock for Frozen {
    fn now(&self) -> Moment {
        // Monday 2 November 2026, noon UTC.
        Moment::from_micros(1_793_620_800 * 1_000_000)
    }
}

/// Answers each prompt as the model asked, and keeps the prompts.
#[derive(Debug, Default)]
struct Model {
    prompts: Mutex<Vec<Prompt>>,
}

impl LanguageModel for Model {
    fn complete(&self, prompt: &Prompt) -> Result<Reply, ModelError> {
        lock(&self.prompts).push(prompt.clone());
        Ok(Reply {
            text: "{\"text\": \"ok\"}".into(),
            model: prompt.model.clone(),
            usage: Usage { input_tokens: 1_000_000, output_tokens: 100_000, ..Usage::default() },
            stop_reason: "stop".into(),
        })
    }
}

/// The AI in force, as a test sets it.
#[derive(Debug)]
struct Chosen(Mutex<InForce>);

impl Engine for Chosen {
    fn in_force(&self) -> InForce {
        lock(&self.0).clone()
    }
}

#[derive(Debug, Default)]
struct Ledger(Mutex<Vec<Spend>>, AtomicI64);

impl UsageLedger for Ledger {
    fn record(&self, spend: &Spend) -> Result<(), Fault> {
        self.1.fetch_add(spend.cost_micros, Ordering::SeqCst);
        lock(&self.0).push(spend.clone());
        Ok(())
    }
    fn spent(&self, _day: &str) -> Result<i64, Fault> {
        Ok(self.1.load(Ordering::SeqCst))
    }
    fn reserved(&self) -> Result<i64, Fault> {
        Ok(0)
    }
    fn in_batches(&self) -> Result<i64, Fault> {
        Ok(0)
    }
}

#[derive(Debug)]
struct NoLimit;

impl AiSettings for NoLimit {
    fn key_named(&self) -> Result<bool, Fault> {
        Ok(true)
    }
    fn daily_limit(&self) -> Result<Option<Value>, Fault> {
        Ok(Some(json!(1000)))
    }
}

struct World {
    model: Arc<Model>,
    chosen: Arc<Chosen>,
    ledger: Arc<Ledger>,
    calls: Calls,
    status: Arc<Status>,
}

impl World {
    fn choose(&self, force: InForce) {
        *lock(&self.chosen.0) = force;
    }

    fn on(&self, provider: AiProvider, model: &str, key_from: &'static str, local: bool) {
        let lm: Arc<dyn LanguageModel> = self.model.clone();
        self.choose(InForce { provider, model: model.into(), key_from, local, lm: Ok(lm) });
    }

    fn off(&self, provider: AiProvider, model: &str, why: Off) {
        self.choose(InForce { provider, model: model.into(), key_from: "", local: false, lm: Err(why) });
    }

    fn counted(&self) -> Vec<(String, String, i64)> {
        lock(&self.ledger.0).iter().map(|s| (s.model.clone(), s.served.clone(), s.cost_micros)).collect()
    }
}

fn world() -> World {
    let model = Arc::new(Model::default());
    let lm: Arc<dyn LanguageModel> = model.clone();
    let chosen = Arc::new(Chosen(Mutex::new(InForce::claude(lm, "ANTHROPIC_API_KEY"))));
    let ledger = Arc::new(Ledger::default());
    let clock = Arc::new(Frozen);
    let budget = Arc::new(Budget {
        ledger: ledger.clone(),
        settings: Arc::new(NoLimit),
        clock: clock.clone(),
        zone: Arc::new(Utc),
    });
    let governor = Arc::new(CostGovernor::new(budget.clone(), clock.clone()));
    let gateway = Arc::new(Gateway::new(chosen.clone(), budget, governor));
    let status = Arc::new(Status::new(gateway.clone(), clock));
    let calls = Calls::new(gateway, status.clone(), false);
    World { model, chosen, ledger, calls, status }
}

#[test]
fn a_call_asks_the_model_in_force_and_is_priced_as_it() {
    let w = world();
    w.on(OpenAi, "gpt-6.1-sol", "mailmind.config.json", false);
    assert_eq!(w.calls.call_claude("Say hello.", "thread"), Ok("{\"text\": \"ok\"}".into()));
    assert_eq!(w.calls.model_label(), "gpt-6.1-sol");
    let prompts = lock(&w.model.prompts).clone();
    assert_eq!(prompts.iter().map(|p| p.model.as_str()).collect::<Vec<_>>(), ["gpt-6.1-sol"]);
    // A million input tokens at $2 and a hundred thousand output at $10 a million.
    assert_eq!(w.counted(), [("gpt-6.1-sol".into(), "gpt-6.1-sol".into(), 3_000_000)]);
}

#[test]
fn a_model_on_this_machine_the_table_doesnt_know_costs_nothing_and_off_it_the_dearest() {
    let w = world();
    w.on(Local, "llama3.2:3b", "", true);
    w.calls.call_claude("Say hello.", "thread").expect("an answer");
    w.on(Compatible, "llama3.2:3b", "mailmind.config.json", false);
    w.calls.call_claude("Say hello.", "thread").expect("an answer");
    let costs: Vec<i64> = w.counted().into_iter().map(|c| c.2).collect();
    // Off this machine, a name of no family is priced as the dearest in the table: $30 in, $180 out.
    assert_eq!(costs, [0, 30_000_000 + 18_000_000]);
}

#[test]
fn with_ai_off_for_any_reason_nothing_is_sent_or_counted_and_the_reason_goes_with_it() {
    let w = world();
    for why in [Off::TurnedOff, Off::NoKey, Off::NoUrl, Off::NoModel] {
        w.off(Compatible, "", why);
        assert_eq!(w.calls.call_claude("Say hello.", "thread"), Err(CallError::NotSent(NotSent::AiOff(why))));
        assert_eq!(w.calls.engine(), "none");
        assert_eq!(w.calls.gateway().held_back("triage", None), None);
    }
    assert!(lock(&w.model.prompts).is_empty());
    assert!(w.counted().is_empty());
}

#[test]
fn the_status_names_the_provider_why_ai_is_off_and_the_model_in_force() {
    let w = world();
    let said = |w: &World| {
        let report = w.status.report().expect("a report");
        (
            report["engine"].clone(),
            report["provider"].clone(),
            report["off"].clone(),
            report["model"].clone(),
            report["key_from"].clone(),
        )
    };
    assert_eq!(
        said(&w),
        (json!("api"), json!("anthropic"), json!(null), json!("claude-opus-5-5"), json!("ANTHROPIC_API_KEY"))
    );
    w.on(Gemini, "gemini-4-hydrogen", "mailmind.config.json", false);
    assert_eq!(
        said(&w),
        (json!("api"), json!("gemini"), json!(null), json!("gemini-4-hydrogen"), json!("mailmind.config.json"))
    );
    w.off(OpenAi, "gpt-6.1-sol", Off::TurnedOff);
    assert_eq!(said(&w), (json!("none"), json!("openai"), json!("turned-off"), json!(""), json!("")));
    w.off(Local, "", Off::NoModel);
    assert_eq!(said(&w), (json!("none"), json!("local"), json!("no-model"), json!(""), json!("")));
    w.off(Compatible, "", Off::NoUrl);
    assert_eq!(said(&w).2, json!("no-url"));
    w.off(Anthropic, "claude-opus-5-5", Off::NoKey);
    assert_eq!(said(&w).2, json!("no-key"));
}

#[test]
fn a_failure_that_reached_nothing_reads_couldnt_reach_it() {
    let plain = |said: &str, status: Option<u16>| ModelError::new(said, status).plain();
    assert_eq!(plain("openai unreachable: connection refused", None), "couldn't reach it");
    assert_eq!(plain("Claude API unreachable: Connection error.", None), "couldn't reach it");
    assert_eq!(plain("gemini unreachable: timed out", None), "it timed out");
    assert_eq!(plain("openai API 401: the key was refused", Some(401)), "the API key was rejected");
    assert_eq!(plain("local API 500: model not found", Some(500)), "the API answered 500");
}

// -- the models a provider lists ------------------------------------------------------------------

/// A provider's list of models as a test sets it: the key kept for each provider, the URL chosen, and what the list
/// answers. It keeps what it was asked.
#[derive(Debug, Default)]
struct Lists {
    keys: Mutex<Vec<AiProvider>>,
    base_url: Mutex<String>,
    answer: Mutex<Option<Result<Vec<String>, ModelError>>>,
    asked: Mutex<Vec<(AiProvider, String, Option<String>)>>,
}

impl ModelLists for Lists {
    fn key(&self, provider: AiProvider) -> Option<Secret> {
        lock(&self.keys).contains(&provider).then(|| Secret::new(format!("sk-{}-fictional", provider.id())))
    }
    fn base_url(&self, _provider: AiProvider) -> String {
        lock(&self.base_url).clone()
    }
    fn list(&self, provider: AiProvider, base_url: &str, key: Option<&Secret>) -> Result<Vec<String>, ModelError> {
        lock(&self.asked).push((provider, base_url.to_owned(), key.map(|k| k.expose().to_owned())));
        lock(&self.answer).clone().unwrap_or_else(|| Ok(vec!["b-model".into(), "a-model".into()]))
    }
}

fn catalog() -> (Arc<Lists>, Catalog) {
    let lists = Arc::new(Lists::default());
    (lists.clone(), Catalog::new(lists))
}

#[test]
fn a_providers_models_are_asked_with_its_key_at_its_url_and_its_own_model_is_the_default() {
    let (lists, catalog) = catalog();
    lock(&lists.keys).extend([Anthropic, OpenAi]);
    assert_eq!(
        catalog.models(OpenAi),
        Ok(json!({"ok": true, "models": ["a-model", "b-model"], "default": "gpt-6.1-sol"}))
    );
    assert_eq!(catalog.models(Local), Ok(json!({"ok": true, "models": ["a-model", "b-model"], "default": null})));
    *lock(&lists.base_url) = "https://llm.example.test/v1".into();
    assert_eq!(catalog.models(Compatible).map(|a| a["default"].clone()), Ok(Value::Null));
    assert_eq!(
        lock(&lists.asked).clone(),
        [
            (OpenAi, "https://api.openai.com/v1".into(), Some("sk-openai-fictional".into())),
            (Local, "http://127.0.0.1:11434/v1".into(), None),
            (Compatible, "https://llm.example.test/v1".into(), None),
        ]
    );
}

#[test]
fn what_cant_be_asked_is_refused_in_words_and_no_answer_is_unreachable() {
    let (lists, catalog) = catalog();
    assert_eq!(catalog.models(Compatible), Err(Problem::refused(NO_URL)));
    assert_eq!(catalog.models(Gemini), Err(Problem::refused(NO_KEY)));
    assert_eq!(catalog.models(Anthropic), Err(Problem::refused(NO_KEY)));
    assert!(lock(&lists.asked).is_empty(), "nothing asked without what it needs");
    lock(&lists.keys).push(Gemini);
    for status in [401, 403] {
        *lock(&lists.answer) = Some(Err(ModelError::new("gemini API: the key was refused", Some(status))));
        assert_eq!(catalog.models(Gemini), Err(Problem::refused(KEY_REFUSED)));
    }
    for status in [None, Some(404), Some(500)] {
        *lock(&lists.answer) = Some(Err(ModelError::new("gemini unreachable: timed out", status)));
        let refused = catalog.models(Gemini).expect_err("no list");
        assert_eq!((refused.kind, refused.said.as_str()), (Kind::Unreachable, UNREACHABLE));
    }
}

// -- the model servers on this Mac ----------------------------------------------------------------

#[derive(Debug)]
struct Asked(Vec<LocalServer>);

impl LocalServers for Asked {
    fn ask(&self) -> Vec<LocalServer> {
        self.0.clone()
    }
}

#[test]
fn the_known_model_servers_are_asked_at_their_usual_addresses_in_this_order() {
    assert_eq!(
        mm_assistant::local_servers::KNOWN,
        [
            ("Ollama", "http://127.0.0.1:11434/v1"),
            ("LM Studio", "http://127.0.0.1:1234/v1"),
            ("llama.cpp", "http://127.0.0.1:8080/v1"),
            ("vLLM", "http://127.0.0.1:8000/v1"),
            ("Jan", "http://127.0.0.1:1337/v1"),
            ("GPT4All", "http://127.0.0.1:4891/v1"),
        ]
    );
    assert_eq!(default_base_url(Local), mm_assistant::local_servers::KNOWN[0].1, "Ollama's is a model on this Mac's");
}

#[test]
fn the_finder_offers_the_servers_that_answered_in_order_with_the_models_that_can_chat() {
    let server = |name: &str, models: Option<&[&str]>| LocalServer {
        name: name.into(),
        url: format!("http://127.0.0.1:1/{name}"),
        models: models.map(|m| m.iter().map(|s| (*s).to_owned()).collect()),
    };
    let finder = Finder::new(Arc::new(Asked(vec![
        server("Ollama", Some(&["qwen3:8b", "nomic-embed-text", "llama3.2:3b", "qwen3:8b"])),
        server("LM Studio", None),
        server("Jan", Some(&[])),
    ])));
    assert_eq!(
        finder.find(),
        json!({"ok": true, "servers": [
            {"name": "Ollama", "url": "http://127.0.0.1:1/Ollama", "models": ["llama3.2:3b", "qwen3:8b"]},
            {"name": "Jan", "url": "http://127.0.0.1:1/Jan", "models": []},
        ]})
    );
    assert_eq!(Finder::new(Arc::new(Asked(vec![]))).find(), json!({"ok": true, "servers": []}));
}
