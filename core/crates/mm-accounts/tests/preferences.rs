//! The preferences block: what a file says, what a change may say, and the AI limit as the Assistant reads it; and AI's
//! switch, provider, model and address (#49).

use std::sync::{Arc, Mutex};

use mm_accounts::Directory;
use mm_accounts::ports::ConfigFile;
use mm_accounts::preferences::{MODEL_MOST, apply, changes, daily_limit_usd, importable, provider, read, switching};
use mm_kernel::ai_provider::AiProvider;
use mm_kernel::errors::{Failure, Fault, Problem};
use mm_kernel::seams::{Clock, Keychain, KeychainProblem, Moment, Secret};
use serde_json::{Map, Value, json};

fn object(value: Value) -> Map<String, Value> {
    match value {
        Value::Object(map) => map,
        _ => Map::new(),
    }
}

#[test]
fn a_file_with_no_block_reads_as_the_defaults() {
    let read = read(&object(json!({"accounts": []})), false);
    assert_eq!(read.len(), 26);
    assert_eq!(read["draft_ahead"], json!(false), "drafting ahead starts off");
    assert_eq!(read["undo_send_seconds"], json!(10));
    assert_eq!(read["theme"], json!("auto"));
    assert_eq!(read["single_keys"], json!(true));
    assert_eq!(read["quiet_until"], json!("07:00"));
    assert_eq!(read.keys().next().map(String::as_str), Some("undo_send_seconds"));
}

#[test]
fn what_a_hand_edit_left_that_cant_be_one_is_the_default() {
    let raw = object(json!({"preferences": {"undo_send_seconds": 10.0, "theme": "teal", "single_keys": 0,
                                            "quiet_from": "24:00", "density": "cozy"}}));
    let read = read(&raw, false);
    assert_eq!(read["undo_send_seconds"], json!(10));
    assert_eq!(read["theme"], json!("auto"));
    assert_eq!(read["single_keys"], json!(true));
    assert_eq!(read["quiet_from"], json!("22:00"));
    assert_eq!(read["density"], json!("cozy"));
}

#[test]
fn a_fresh_install_follows_the_system_and_the_clock_and_a_theme_the_file_holds_stands() {
    assert_eq!(read(&object(json!({"accounts": []})), false)["theme"], json!("auto"));
    assert_eq!(read(&object(json!({"preferences": {"theme": "day"}})), false)["theme"], json!("day"));
    let wanted = changes(&object(json!({"theme": "auto"}))).unwrap_or_default();
    assert_eq!(wanted.get("theme"), Some(&json!("auto")));
}

#[test]
fn one_value_that_cant_be_refuses_the_whole_change() {
    let asked = object(json!({"theme": "night", "undo_send_seconds": 61}));
    let refused = changes(&asked).err().map(|p| p.said);
    assert_eq!(refused.as_deref(), Some("The Undo Send window must be 5 to 60 seconds."));
    let asked = object(json!({"ai_daily_limit_usd": 0.0}));
    let refused = changes(&asked).err().map(|p| p.said);
    assert_eq!(refused.as_deref(), Some("The AI daily limit must be more than $0 and at most $1000."));
    let asked = object(json!({"density": null}));
    assert_eq!(changes(&asked).err().map(|p| p.said).as_deref(), Some("That can\u{2019}t be the density setting."));
    let asked = object(json!({"quiet_from": "soon"}));
    assert_eq!(changes(&asked).err().map(|p| p.said).as_deref(), Some("Quiet hours need a time such as 22:00."));
    let asked = object(json!({"quiet_until": "soon"}));
    assert_eq!(changes(&asked).err().map(|p| p.said).as_deref(), Some("Quiet hours need a time such as 07:00."));
}

#[test]
fn a_change_writes_the_block_and_the_limit_at_the_top() {
    let mut raw = object(json!({"accounts": [], "preferences": "lost"}));
    let wanted = changes(&object(json!({"theme": "dusk", "ai_daily_limit_usd": 12.5, "other": 1}))).unwrap_or_default();
    apply(&mut raw, &wanted);
    assert_eq!(
        Value::Object(raw),
        json!({"accounts": [], "preferences": {"theme": "dusk"}, "ai_daily_limit_usd": 12.5})
    );
}

#[test]
fn an_import_takes_only_what_the_file_doesnt_say() {
    let raw = object(json!({"preferences": {"theme": "night"}}));
    let held = object(json!({"theme": "dusk", "density": "compact", "read_size": "huge", "ai_daily_limit_usd": 3}));
    assert_eq!(Value::Object(importable(&held, &raw)), json!({"density": "compact"}));
}

#[test]
fn the_daily_limit_reads_as_python_reads_it() {
    assert_eq!(daily_limit_usd(&object(json!({}))), 15.0);
    assert_eq!(daily_limit_usd(&object(json!({"ai_daily_limit_usd": 12}))), 12.0);
    assert_eq!(daily_limit_usd(&object(json!({"ai_daily_limit_usd": " 1_2.5 "}))), 12.5);
    assert_eq!(daily_limit_usd(&object(json!({"ai_daily_limit_usd": "1__2"}))), 15.0);
    assert_eq!(daily_limit_usd(&object(json!({"ai_daily_limit_usd": true}))), 1.0);
    assert_eq!(daily_limit_usd(&object(json!({"ai_daily_limit_usd": -3}))), 15.0);
    assert_eq!(daily_limit_usd(&object(json!({"ai_daily_limit_usd": 0.0000005}))), 0.0);
}

// -- AI's switch, its provider, model and address (#49) ------------------------------------------------------------

#[test]
fn a_fresh_file_has_ai_mode_off_with_claude_its_own_model_and_its_usual_address() {
    let read = read(&object(json!({"accounts": []})), false);
    assert_eq!(
        (&read["ai_enabled"], &read["ai_provider"], &read["ai_model"], &read["ai_base_url"]),
        (&json!(false), &json!("anthropic"), &json!(""), &json!(""))
    );
    let names: Vec<&str> = read.keys().map(String::as_str).collect();
    assert_eq!(names[21..], ["draft_ahead", "ai_enabled", "ai_provider", "ai_model", "ai_base_url"]);
    assert_eq!(provider(&object(json!({}))), AiProvider::Anthropic);
}

#[test]
fn ai_mode_the_file_doesnt_set_is_on_where_there_is_a_key_to_ask_with() {
    let mode = |raw: Value, key_in_environment: bool| read(&object(raw), key_in_environment)["ai_enabled"].clone();
    assert_eq!(mode(json!({}), true), json!(true), "ANTHROPIC_API_KEY set");
    assert_eq!(mode(json!({"anthropic_api_key": {"keychain": "anthropic-api-key", "version": 1}}), false), json!(true));
    assert_eq!(mode(json!({"local_api_key": {"keychain": "local-api-key"}}), false), json!(true), "any provider's");
    assert_eq!(mode(json!({"gemini_api_key": {"op": "op://Private/Gemini/credential"}}), false), json!(true));
    assert_eq!(mode(json!({"openai_api_key": {}, "compatible_api_key": null}), false), json!(false), "naming none");
    assert_eq!(mode(json!({"other_api_key": {"keychain": "x"}}), false), json!(false), "no provider's");
    // What the file sets stands, either way.
    let off = json!({"anthropic_api_key": {"keychain": "anthropic-api-key"}, "preferences": {"ai_enabled": false}});
    assert_eq!(mode(off, true), json!(false));
    assert_eq!(mode(json!({"preferences": {"ai_enabled": true}}), false), json!(true));
    // A value that can't be one is no setting.
    assert_eq!(mode(json!({"preferences": {"ai_enabled": "yes"}}), false), json!(false));
    assert_eq!(mode(json!({"preferences": {"ai_enabled": 1}}), true), json!(true));
}

#[test]
fn settings_answer_ai_mode_in_force_and_keep_it_once_set() {
    let file = Arc::new(File(Mutex::new(object(json!({"accounts": []})))));
    let directory = Directory::new(file.clone(), Arc::new(NoKeychain), Arc::new(At));
    let mode = |answer: Value| answer["preferences"]["ai_enabled"].clone();
    assert_eq!(mode(directory.preferences().expect("read")), json!(false), "a fresh install");
    file.write(&object(json!({"accounts": [], "openai_api_key": {"keychain": "openai-api-key"}}))).expect("written");
    assert_eq!(mode(directory.preferences().expect("read")), json!(true), "a key is named");
    assert_eq!(mode(directory.set_preferences(&object(json!({"ai_enabled": false}))).expect("kept")), json!(false));
    assert_eq!(file.read().expect("the file")["preferences"], json!({"ai_enabled": false}));
    assert_eq!(mode(directory.preferences().expect("read")), json!(false));
}

#[test]
fn settings_count_claudes_key_in_the_environment_toward_ai_mode() {
    let file = Arc::new(File(Mutex::new(object(json!({"accounts": []})))));
    let with = |given: Option<&str>| {
        let variables = Arc::new(Given(given.map(str::to_owned)));
        let directory = Directory::new(file.clone(), Arc::new(NoKeychain), Arc::new(At)).reading_variables(variables);
        directory.preferences().expect("read")["preferences"]["ai_enabled"].clone()
    };
    assert_eq!(with(Some("sk-ant-fictional")), json!(true));
    assert_eq!(with(Some("  ")), json!(false), "blank is none");
    assert_eq!(with(None), json!(false));
}

/// The environment, giving `ANTHROPIC_API_KEY` alone.
#[derive(Debug)]
struct Given(Option<String>);

impl mm_accounts::ports::Variables for Given {
    fn variable(&self, name: &str) -> Option<Secret> {
        self.0.clone().filter(|_| name == "ANTHROPIC_API_KEY").map(Secret::new)
    }
}

#[test]
fn an_ai_setting_a_hand_edit_left_that_cant_be_one_is_its_default() {
    let raw = object(json!({"preferences": {"ai_enabled": "no", "ai_provider": "claude",
                                            "ai_model": "gpt 6", "ai_base_url": "http://api.example.test/v1"}}));
    let said = read(&raw, true);
    assert_eq!(
        (&said["ai_enabled"], &said["ai_provider"], &said["ai_model"], &said["ai_base_url"]),
        (&json!(true), &json!("anthropic"), &json!(""), &json!(""))
    );
    assert_eq!(provider(&raw), AiProvider::Anthropic);

    let raw = object(json!({"preferences": {"ai_enabled": false, "ai_provider": "local",
                                            "ai_model": "llama3.2:3b", "ai_base_url": "http://localhost:11434/v1/"}}));
    let read = read(&raw, true);
    assert_eq!(
        (&read["ai_enabled"], &read["ai_provider"], &read["ai_model"], &read["ai_base_url"]),
        (&json!(false), &json!("local"), &json!("llama3.2:3b"), &json!("http://localhost:11434/v1"))
    );
    assert_eq!(provider(&raw), AiProvider::Local);
}

#[test]
fn a_change_keeps_a_model_as_given_and_an_address_without_its_trailing_slash() {
    let asked = object(json!({"ai_enabled": false, "ai_provider": "compatible",
                              "ai_model": "openai/gpt-6.1-sol", "ai_base_url": "https://llm.example.test/api/v1/"}));
    assert_eq!(
        changes(&asked).map(Value::Object),
        Ok(json!({"ai_enabled": false, "ai_provider": "compatible",
                  "ai_model": "openai/gpt-6.1-sol", "ai_base_url": "https://llm.example.test/api/v1"}))
    );
    let cleared = object(json!({"ai_model": "", "ai_base_url": ""}));
    assert_eq!(changes(&cleared).map(Value::Object), Ok(json!({"ai_model": "", "ai_base_url": ""})));
    let longest = "m".repeat(MODEL_MOST);
    let asked = object(json!({"ai_model": longest}));
    assert_eq!(changes(&asked).map(|w| w["ai_model"].clone()), Ok(json!(longest)));
}

#[test]
fn an_ai_setting_that_cant_be_one_refuses_the_change_in_words() {
    const MODEL: &str = "The AI model\u{2019}s name must be up to 200 letters, digits and . _ : / @ + - characters.";
    const URL: &str = "The AI service\u{2019}s URL must be https, or http on this Mac, in at most 500 characters, with \
                       no user, password, query or fragment.";
    for (asked, said) in [
        (json!({"ai_enabled": "yes"}), "That can\u{2019}t be the ai_enabled setting."),
        (json!({"ai_enabled": null}), "That can\u{2019}t be the ai_enabled setting."),
        (json!({"ai_provider": "claude"}), "That can\u{2019}t be the ai_provider setting."),
        (json!({"ai_model": "gpt 6"}), MODEL),
        (json!({"ai_model": "m".repeat(201)}), MODEL),
        (json!({"ai_model": "modèle"}), MODEL),
        (json!({"ai_model": null}), MODEL),
        (json!({"ai_model": 6}), MODEL),
        (json!({"ai_base_url": "http://llm.example.test/v1"}), URL),
        (json!({"ai_base_url": "https://who:secret@llm.example.test/v1"}), URL),
        (json!({"ai_base_url": "https://llm.example.test/v1?key=1"}), URL),
        (json!({"ai_base_url": format!("https://llm.example.test/{}", "a".repeat(500))}), URL),
        (json!({"ai_base_url": null}), URL),
        (json!({"ai_base_url": "https://llm.example.test/caf\u{e9}"}), URL),
        (json!({"ai_base_url": "https://llm.example.test:/v1"}), URL),
        (json!({"ai_base_url": "http://127.0.0.1:/v1"}), URL),
        (json!({"ai_base_url": "http://[::1]:/v1"}), URL),
        (json!({"ai_base_url": "https://llm.example.test:0/v1"}), URL),
    ] {
        let refused = changes(&object(asked.clone())).err().map(|p| p.said);
        assert_eq!(refused.as_deref(), Some(said), "{asked}");
        assert!(!refused.unwrap_or_default().contains("secret"), "nothing asked is said back");
    }
}

#[test]
fn a_change_to_another_provider_clears_the_model_and_the_address_it_doesnt_name() {
    let none = object(json!({}));
    let to = |asked: Value, raw: &Map<String, Value>| {
        Value::Object(switching(changes(&object(asked)).unwrap_or_default(), raw))
    };
    assert_eq!(
        to(json!({"ai_provider": "openai"}), &none),
        json!({"ai_provider": "openai", "ai_model": "", "ai_base_url": ""})
    );
    assert_eq!(
        to(json!({"ai_provider": "local", "ai_model": "llama3.2"}), &none),
        json!({"ai_provider": "local", "ai_model": "llama3.2", "ai_base_url": ""})
    );
    assert_eq!(
        to(json!({"ai_provider": "compatible", "ai_base_url": "https://llm.example.test/v1"}), &none),
        json!({"ai_provider": "compatible", "ai_base_url": "https://llm.example.test/v1", "ai_model": ""})
    );
    // The same provider again changes nothing else.
    assert_eq!(to(json!({"ai_provider": "anthropic"}), &none), json!({"ai_provider": "anthropic"}));
    let gemini = object(json!({"preferences": {"ai_provider": "gemini", "ai_model": "gemini-3.6-flash"}}));
    assert_eq!(
        to(json!({"ai_provider": "gemini", "theme": "dusk"}), &gemini),
        json!({"ai_provider": "gemini", "theme": "dusk"})
    );
    assert_eq!(
        to(json!({"ai_provider": "anthropic"}), &gemini),
        json!({"ai_provider": "anthropic", "ai_model": "", "ai_base_url": ""})
    );
    // A change that names no provider leaves the model and the address alone.
    assert_eq!(to(json!({"ai_enabled": false}), &gemini), json!({"ai_enabled": false}));
}

#[derive(Debug)]
struct File(Mutex<Map<String, Value>>);

impl ConfigFile for File {
    fn read(&self) -> Result<Map<String, Value>, Fault> {
        Ok(self.0.lock().unwrap_or_else(std::sync::PoisonError::into_inner).clone())
    }

    fn write(&self, raw: &Map<String, Value>) -> Result<(), Fault> {
        *self.0.lock().unwrap_or_else(std::sync::PoisonError::into_inner) = raw.clone();
        Ok(())
    }
}

#[derive(Debug)]
struct NoKeychain;

impl Keychain for NoKeychain {
    fn find(&self, _item: &str) -> Result<Option<Secret>, KeychainProblem> {
        Ok(None)
    }
}

#[derive(Debug)]
struct At;

impl Clock for At {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000)
    }
}

#[test]
fn settings_keep_a_switch_of_provider_without_the_last_ones_model_and_address() {
    let file = Arc::new(File(Mutex::new(object(json!({"accounts": [], "preferences": {
        "ai_provider": "compatible", "ai_model": "local-model", "ai_base_url": "https://llm.example.test/v1"}})))));
    let directory = Directory::new(file.clone(), Arc::new(NoKeychain), Arc::new(At));
    let answer = directory.set_preferences(&object(json!({"ai_provider": "openai"}))).expect("kept");
    let said = &answer["preferences"];
    assert_eq!(
        (&said["ai_provider"], &said["ai_model"], &said["ai_base_url"], &said["ai_enabled"]),
        (&json!("openai"), &json!(""), &json!(""), &json!(false))
    );
    let raw = file.read().expect("the file");
    assert_eq!(raw["preferences"], json!({"ai_provider": "openai", "ai_model": "", "ai_base_url": ""}));

    let refused = directory.set_preferences(&object(json!({"ai_provider": "gemini", "ai_base_url": "ftp://x"})));
    assert!(matches!(refused, Err(Failure::Problem(_))));
    assert_eq!(file.read().expect("the file")["preferences"]["ai_provider"], json!("openai"), "nothing was written");
}

#[test]
fn a_model_on_this_mac_is_at_an_address_on_this_mac_or_refused_in_words() {
    let file = Arc::new(File(Mutex::new(object(json!({"accounts": [], "preferences": {
        "ai_provider": "compatible", "ai_model": "mixtral", "ai_base_url": "https://llm.example.test/v1"}})))));
    let directory = Directory::new(file.clone(), Arc::new(NoKeychain), Arc::new(At));
    let set = |asked: Value| directory.set_preferences(&object(asked)).map(|answer| answer["preferences"].clone());
    let refused = |words: &str| Err(Failure::Problem(Problem::refused(words)));
    let elsewhere = "A model on this Mac must be at 127.0.0.1, ::1 or localhost.";
    assert_eq!(set(json!({"ai_provider": "local", "ai_base_url": "https://llm.example.test/v1"})), refused(elsewhere));
    assert_eq!(file.read().expect("the file")["preferences"]["ai_provider"], json!("compatible"), "nothing written");
    // Switched without an address, it is at its usual one.
    let said = set(json!({"ai_provider": "local"})).expect("kept");
    assert_eq!((&said["ai_provider"], &said["ai_base_url"]), (&json!("local"), &json!("")));
    assert_eq!(set(json!({"ai_base_url": "https://10.0.0.5/v1"})), refused(elsewhere));
    let plain = "The AI service\u{2019}s URL must be https, or http on this Mac, in at most 500 characters, with no \
                 user, password, query or fragment.";
    assert_eq!(
        set(json!({"ai_base_url": "http://192.168.1.20:11434/v1"})),
        refused(plain),
        "any provider's rule first"
    );
    for here in
        ["http://127.0.0.1:1234/v1/", "https://localhost:8443/v1", "http://[::1]:8080/v1", "HTTP://LOCALHOST/v1"]
    {
        let said = set(json!({"ai_base_url": here})).expect("on this Mac");
        assert_eq!(said["ai_base_url"], json!(here.trim_end_matches('/')), "{here}");
    }
    // Back to a server elsewhere, as a compatible service.
    let said = set(json!({"ai_provider": "compatible", "ai_base_url": "https://llm.example.test/v1"})).expect("kept");
    assert_eq!(said["ai_base_url"], json!("https://llm.example.test/v1"));
}

#[test]
fn a_model_on_this_mac_a_hand_edit_left_elsewhere_reads_at_its_usual_address_and_other_changes_go_on() {
    let elsewhere =
        json!({"accounts": [], "preferences": {"ai_provider": "local", "ai_base_url": "https://llm.example.test/v1"}});
    assert_eq!(read(&object(elsewhere.clone()), false)["ai_base_url"], json!(""));
    let compatible =
        object(json!({"preferences": {"ai_provider": "compatible", "ai_base_url": "https://llm.example.test/v1"}}));
    assert_eq!(read(&compatible, false)["ai_base_url"], json!("https://llm.example.test/v1"));

    let file = Arc::new(File(Mutex::new(object(elsewhere))));
    let directory = Directory::new(file.clone(), Arc::new(NoKeychain), Arc::new(At));
    let answer = directory.set_preferences(&object(json!({"theme": "dusk", "ai_model": "qwen3:8b"}))).expect("kept");
    assert_eq!((&answer["preferences"]["theme"], &answer["preferences"]["ai_base_url"]), (&json!("dusk"), &json!("")));
}
