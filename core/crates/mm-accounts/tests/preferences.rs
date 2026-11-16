//! The preferences block: what a file says, what a change may say, and the AI limit as the Assistant reads it; and AI's
//! switch, provider, model and address (#49).

use std::sync::{Arc, Mutex};

use mm_accounts::Directory;
use mm_accounts::ports::ConfigFile;
use mm_accounts::preferences::{MODEL_MOST, apply, changes, daily_limit_usd, importable, provider, read, switching};
use mm_kernel::ai_provider::AiProvider;
use mm_kernel::errors::{Failure, Fault};
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
    let read = read(&object(json!({"accounts": []})));
    assert_eq!(read.len(), 25);
    assert_eq!(read["undo_send_seconds"], json!(10));
    assert_eq!(read["theme"], json!("day"));
    assert_eq!(read["single_keys"], json!(true));
    assert_eq!(read["quiet_until"], json!("07:00"));
    assert_eq!(read.keys().next().map(String::as_str), Some("undo_send_seconds"));
}

#[test]
fn what_a_hand_edit_left_that_cant_be_one_is_the_default() {
    let raw = object(json!({"preferences": {"undo_send_seconds": 10.0, "theme": "teal", "single_keys": 0,
                                            "quiet_from": "24:00", "density": "cozy"}}));
    let read = read(&raw);
    assert_eq!(read["undo_send_seconds"], json!(10));
    assert_eq!(read["theme"], json!("day"));
    assert_eq!(read["single_keys"], json!(true));
    assert_eq!(read["quiet_from"], json!("22:00"));
    assert_eq!(read["density"], json!("cozy"));
}

#[test]
fn one_value_that_cant_be_refuses_the_whole_change() {
    let asked = object(json!({"theme": "night", "undo_send_seconds": 61}));
    let refused = changes(&asked).err().map(|p| p.said);
    assert_eq!(refused.as_deref(), Some("the Undo Send window must be 5 to 60 seconds"));
    let asked = object(json!({"ai_daily_limit_usd": 0.0}));
    let refused = changes(&asked).err().map(|p| p.said);
    assert_eq!(refused.as_deref(), Some("the AI daily limit must be more than $0 and at most $1000"));
    let asked = object(json!({"density": null}));
    assert_eq!(changes(&asked).err().map(|p| p.said).as_deref(), Some("density can't be that"));
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
fn ai_is_on_with_claude_its_own_model_and_its_usual_address_when_the_file_says_nothing() {
    let read = read(&object(json!({"accounts": []})));
    assert_eq!(
        (&read["ai_enabled"], &read["ai_provider"], &read["ai_model"], &read["ai_base_url"]),
        (&json!(true), &json!("anthropic"), &json!(""), &json!(""))
    );
    let names: Vec<&str> = read.keys().map(String::as_str).collect();
    assert_eq!(names[21..], ["ai_enabled", "ai_provider", "ai_model", "ai_base_url"]);
    assert_eq!(provider(&object(json!({}))), AiProvider::Anthropic);
}

#[test]
fn an_ai_setting_a_hand_edit_left_that_cant_be_one_is_its_default() {
    let raw = object(json!({"preferences": {"ai_enabled": "no", "ai_provider": "claude",
                                            "ai_model": "gpt 6", "ai_base_url": "http://api.example.test/v1"}}));
    let said = read(&raw);
    assert_eq!(
        (&said["ai_enabled"], &said["ai_provider"], &said["ai_model"], &said["ai_base_url"]),
        (&json!(true), &json!("anthropic"), &json!(""), &json!(""))
    );
    assert_eq!(provider(&raw), AiProvider::Anthropic);

    let raw = object(json!({"preferences": {"ai_enabled": false, "ai_provider": "ollama",
                                            "ai_model": "llama3.2:3b", "ai_base_url": "http://localhost:11434/v1/"}}));
    let read = read(&raw);
    assert_eq!(
        (&read["ai_enabled"], &read["ai_provider"], &read["ai_model"], &read["ai_base_url"]),
        (&json!(false), &json!("ollama"), &json!("llama3.2:3b"), &json!("http://localhost:11434/v1"))
    );
    assert_eq!(provider(&raw), AiProvider::Ollama);
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
    const MODEL: &str = "the AI model must be up to 200 letters, digits and . _ : / @ + -";
    const URL: &str = "the AI service's URL must be https, or http on this Mac, in at most 500 characters, with no \
                       user, password, query or fragment";
    for (asked, said) in [
        (json!({"ai_enabled": "yes"}), "ai_enabled can't be that"),
        (json!({"ai_enabled": null}), "ai_enabled can't be that"),
        (json!({"ai_provider": "claude"}), "ai_provider can't be that"),
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
        to(json!({"ai_provider": "ollama", "ai_model": "llama3.2"}), &none),
        json!({"ai_provider": "ollama", "ai_model": "llama3.2", "ai_base_url": ""})
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
        (&json!("openai"), &json!(""), &json!(""), &json!(true))
    );
    let raw = file.read().expect("the file");
    assert_eq!(raw["preferences"], json!({"ai_provider": "openai", "ai_model": "", "ai_base_url": ""}));

    let refused = directory.set_preferences(&object(json!({"ai_provider": "gemini", "ai_base_url": "ftp://x"})));
    assert!(matches!(refused, Err(Failure::Problem(_))));
    assert_eq!(file.read().expect("the file")["preferences"]["ai_provider"], json!("openai"), "nothing was written");
}
