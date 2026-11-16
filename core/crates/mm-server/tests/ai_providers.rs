//! AI's switch, its provider and each provider's key on the Rust server (#49), over a socket: the key routes, the
//! preferences that choose the provider, what the status says of AI and why it is off, a call going to the provider
//! chosen and nowhere when AI is off, the models a provider lists, and the background reaching nothing with AI turned
//! off. Every provider is a stand-in on loopback, and the Keychain a stand-in in memory.

#![allow(clippy::unwrap_used)]

use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use mm_kernel::seams::{Clock, Keychain, KeychainProblem, Moment, Outside, Secret, Tripped};
use mm_server::environment::{Environment, Settings};
use mm_server::real::Sanitizing;
use mm_server::{App, Seams, Server};
use mm_store::{Database, DbKey};
use serde_json::{Value, json};

#[derive(Debug)]
struct Frozen;

impl Clock for Frozen {
    fn now(&self) -> Moment {
        // Monday 2 November 2026, noon UTC.
        Moment::from_micros(1_793_620_800_000_000)
    }
}

/// Reaches only this machine, and remembers each host it was asked about.
#[derive(Debug, Default)]
struct Loopback(Mutex<Vec<String>>);

impl Outside for Loopback {
    fn open(&self, _path: &Path) -> Result<(), Tripped> {
        Ok(())
    }
    fn reach(&self, host: &str) -> Result<(), Tripped> {
        self.0.lock().unwrap().push(host.to_owned());
        if host == "127.0.0.1" { Ok(()) } else { Err(Tripped(host.to_owned())) }
    }
    fn run(&self, program: &str) -> Result<(), Tripped> {
        Err(Tripped(program.to_owned()))
    }
}

/// The login Keychain's stand-in: what it keeps, in memory, and how often it was asked.
#[derive(Debug, Default)]
struct Kept {
    items: Mutex<HashMap<String, String>>,
    asked: Mutex<usize>,
}

impl Keychain for Kept {
    fn find(&self, item: &str) -> Result<Option<Secret>, KeychainProblem> {
        *self.asked.lock().unwrap() += 1;
        Ok(self.items.lock().unwrap().get(item).map(|s| Secret::new(s.clone())))
    }
    fn put(&self, item: &str, secret: &Secret) -> Result<(), KeychainProblem> {
        self.items.lock().unwrap().insert(item.to_owned(), secret.expose().to_owned());
        Ok(())
    }
    fn delete(&self, item: &str) -> Result<bool, KeychainProblem> {
        Ok(self.items.lock().unwrap().remove(item).is_some())
    }
}

struct Running {
    app: Arc<App>,
    port: u16,
    home: tempfile::TempDir,
    keychain: Arc<Kept>,
    outside: Arc<Loopback>,
    _runtime: tokio::runtime::Runtime,
}

/// The server over a data directory holding `config`, with `pairs` in its environment.
fn running(config: &Value, pairs: &[(&str, &str)]) -> Running {
    let home = tempfile::tempdir().unwrap();
    std::fs::write(home.path().join("mailmind.config.json"), config.to_string()).unwrap();
    let (path, key) = (home.path().join("mailmind.db"), DbKey::from_bytes([0x5e; 32]));
    Database::new(path, key, Arc::new(Loopback::default())).ready(&Frozen).unwrap();
    let runtime = tokio::runtime::Builder::new_multi_thread().enable_all().build().unwrap();
    let server = runtime.block_on(Server::bind(0)).unwrap();
    let port = server.port().unwrap();
    let key = "5e".repeat(32);
    let environment = Environment::from_pairs([("MAILMIND_DB_KEY", key.as_str())].into_iter().chain(pairs.to_vec()));
    let settings = Settings { home: home.path().to_path_buf() };
    let (keychain, outside) = (Arc::new(Kept::default()), Arc::new(Loopback::default()));
    let seams = Seams {
        clock: Arc::new(Frozen),
        outside: outside.clone(),
        keychain: keychain.clone(),
        sanitizer: Arc::new(Sanitizing),
        mail_hosts: Arc::new(mm_server::mail_hosts::NoLookups),
    };
    let app = Arc::new(App::assemble(&environment, &settings, home.path().join("dist"), port, &seams).unwrap());
    let (stop, stopped) = tokio::sync::watch::channel(false);
    runtime.spawn(server.run(app.clone(), stopped, Duration::from_secs(1)));
    std::mem::forget(stop);
    Running { app, port, home, keychain, outside, _runtime: runtime }
}

struct Answer {
    status: u16,
    body: Vec<u8>,
}

impl Answer {
    fn json(&self) -> Value {
        serde_json::from_slice(&self.body).unwrap()
    }

    fn text(&self) -> String {
        String::from_utf8_lossy(&self.body).into_owned()
    }
}

impl Running {
    fn config(&self) -> PathBuf {
        self.home.path().join("mailmind.config.json")
    }

    /// The config file as `config` says, in place of what it held.
    fn write_config(&self, config: &Value) {
        std::fs::write(self.config(), config.to_string()).unwrap();
    }

    fn ask(&self, method: &str, path: &str, body: Option<&Value>) -> Answer {
        let own = format!("127.0.0.1:{}", self.port);
        let mut stream = TcpStream::connect(("127.0.0.1", self.port)).unwrap();
        stream.set_read_timeout(Some(Duration::from_secs(20))).unwrap();
        let sent = body.map(Value::to_string).unwrap_or_default();
        let mut head = format!("{method} {path} HTTP/1.1\r\nHost: {own}\r\nSec-Fetch-Site: same-origin\r\n");
        if method == "POST" {
            head.push_str(&format!(
                "Origin: http://{own}\r\nX-CSRF-Token: {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\n",
                self.app.csrf,
                sent.len()
            ));
        }
        head.push_str("Connection: close\r\n\r\n");
        stream.write_all(head.as_bytes()).unwrap();
        stream.write_all(sent.as_bytes()).unwrap();
        let mut got = Vec::new();
        let _ = stream.read_to_end(&mut got);
        let end = got.windows(4).position(|w| w == b"\r\n\r\n").unwrap();
        let line = String::from_utf8(got[..end].to_vec()).unwrap();
        let status = line.split(' ').nth(1).unwrap().parse().unwrap();
        Answer { status, body: got[end + 4..].to_vec() }
    }

    fn get(&self, path: &str) -> Answer {
        self.ask("GET", path, None)
    }

    fn post(&self, path: &str, body: &Value) -> Answer {
        self.ask("POST", path, Some(body))
    }

    /// What the status says of AI: engine, provider, off, model and key_from.
    fn ai(&self) -> Value {
        let ai = self.get("/api/digest").json()["ai"].clone();
        json!([ai["engine"], ai["provider"], ai["off"], ai["model"], ai["key_from"]])
    }
}

/// A request as a provider's stand-in read it: its request line, lower-cased header names, and its JSON body.
#[derive(Clone, Debug)]
struct Heard {
    line: String,
    headers: Vec<(String, String)>,
    body: Value,
}

impl Heard {
    fn header(&self, name: &str) -> Option<&str> {
        self.headers.iter().find(|(n, _)| n == name).map(|(_, v)| v.as_str())
    }
}

/// A provider's stand-in at `/v1` on loopback, answering each request with the next of `answers` (a status and a
/// body) and keeping what it heard.
fn provider(answers: Vec<(u16, Value)>) -> (String, Arc<Mutex<Vec<Heard>>>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://127.0.0.1:{}/v1", listener.local_addr().unwrap().port());
    let heard = Arc::new(Mutex::new(Vec::new()));
    let kept = heard.clone();
    std::thread::spawn(move || {
        for (status, answer) in answers {
            let Ok((mut stream, _)) = listener.accept() else { return };
            let mut read = Vec::new();
            let mut chunk = [0u8; 8192];
            let (end, length) = loop {
                let n = stream.read(&mut chunk).unwrap_or(0);
                if n == 0 {
                    return;
                }
                read.extend_from_slice(&chunk[..n]);
                if let Some(end) = read.windows(4).position(|w| w == b"\r\n\r\n") {
                    let head = String::from_utf8_lossy(&read[..end]).to_lowercase();
                    let length = head
                        .lines()
                        .find_map(|l| l.strip_prefix("content-length:").map(|v| v.trim().parse::<usize>().unwrap_or(0)))
                        .unwrap_or(0);
                    break (end, length);
                }
            };
            while read.len() < end + 4 + length {
                let n = stream.read(&mut chunk).unwrap_or(0);
                if n == 0 {
                    break;
                }
                read.extend_from_slice(&chunk[..n]);
            }
            let head = String::from_utf8_lossy(&read[..end]).into_owned();
            let mut lines = head.split("\r\n");
            let line = lines.next().unwrap_or_default().to_owned();
            let headers =
                lines.filter_map(|l| l.split_once(':')).map(|(n, v)| (n.trim().to_lowercase(), v.trim().to_owned()));
            let body = serde_json::from_slice(&read[end + 4..]).unwrap_or(Value::Null);
            kept.lock().unwrap().push(Heard { line, headers: headers.collect(), body });
            let said = answer.to_string();
            let reply = format!(
                "HTTP/1.0 {status} X\r\ncontent-type: application/json\r\ncontent-length: {}\r\n\r\n{said}",
                said.len()
            );
            let _ = stream.write_all(reply.as_bytes());
        }
    });
    (url, heard)
}

fn heard(log: &Arc<Mutex<Vec<Heard>>>) -> Vec<Heard> {
    log.lock().unwrap().clone()
}

/// A chat completion that answers `said` as JSON.
fn completion(said: &Value) -> Value {
    json!({"model": "llama3.2:3b", "choices": [{"message": {"role": "assistant", "content": said.to_string()},
           "finish_reason": "stop"}], "usage": {"prompt_tokens": 100, "completion_tokens": 10}})
}

/// A port nothing listens on.
fn closed_port() -> u16 {
    TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port()
}

fn refused(detail: &str) -> Value {
    json!({"type": "/problems/refused", "title": "Refused", "status": 400, "detail": detail})
}

// -- each provider's key ---------------------------------------------------------------

#[test]
fn each_providers_key_is_kept_and_named_and_never_said() {
    let r = running(&json!({}), &[]);
    assert_eq!((r.get("/api/ai/key").status, r.get("/api/ai/key").json()), (200, json!({"ok": true, "kept": []})));
    let keep = |provider: &str| r.post("/api/ai/key", &json!({"provider": provider, "key": "sk-fictional-secret"}));
    let kept = keep("gemini");
    assert_eq!((kept.status, kept.json()), (200, json!({"ok": true, "kept": ["gemini"]})));
    keep("openai");
    let kept = keep("anthropic");
    assert_eq!(kept.json(), json!({"ok": true, "kept": ["anthropic", "openai", "gemini"]}), "the closed set's order");
    let kept = keep("compatible");
    assert_eq!(kept.json()["kept"], json!(["anthropic", "openai", "gemini", "compatible"]));
    assert!(!kept.text().contains("fictional"));

    let kept = keep("local");
    assert_eq!(kept.json()["kept"], json!(["anthropic", "openai", "gemini", "local", "compatible"]));
    for wrong in [
        json!({"provider": "claude", "key": "k"}),
        json!({"provider": "ollama", "key": "k"}),
        json!({"key": "k"}),
        json!({"provider": "openai"}),
    ] {
        let refused = r.post("/api/ai/key", &wrong);
        assert_eq!((refused.status, refused.json()["detail"].clone()), (400, json!("bad request")), "{wrong}");
    }

    let cleared = r.post("/api/ai/key/clear", &json!({"provider": "openai"}));
    assert_eq!(
        (cleared.status, cleared.json()),
        (200, json!({"ok": true, "kept": ["anthropic", "gemini", "local", "compatible"]}))
    );
    let cleared = r.post("/api/ai/key/clear", &json!({"provider": "ollama"}));
    assert_eq!((cleared.status, cleared.json()["detail"].clone()), (400, json!("bad request")));
    assert_eq!(r.get("/api/ai/key").json()["kept"], json!(["anthropic", "gemini", "local", "compatible"]));

    let items = r.keychain.items.lock().unwrap().clone();
    let mut names: Vec<&str> = items.keys().map(String::as_str).collect();
    names.sort_unstable();
    assert_eq!(names, ["anthropic-api-key", "compatible-api-key", "gemini-api-key", "local-api-key"]);
    let config = std::fs::read_to_string(r.config()).unwrap();
    assert!(!config.contains("fictional"), "the file names where the key is, never the key");
    assert!(config.contains("gemini-api-key"));
}

// -- the preferences that choose the provider ---------------------------------------------

#[test]
fn the_ai_preferences_change_through_the_route_and_another_provider_clears_its_model_and_url() {
    let r = running(&json!({}), &[]);
    let ai = |r: &Running| {
        let said = r.get("/api/preferences").json()["preferences"].clone();
        json!([said["ai_enabled"], said["ai_provider"], said["ai_model"], said["ai_base_url"]])
    };
    assert_eq!(ai(&r), json!([false, "anthropic", "", ""]), "a fresh install");
    let asked = json!({"ai_enabled": true, "ai_provider": "compatible", "ai_model": "mixtral-8x7b",
                       "ai_base_url": "https://llm.example.test/v1/"});
    assert_eq!(r.post("/api/preferences", &asked).status, 200);
    assert_eq!(ai(&r), json!([true, "compatible", "mixtral-8x7b", "https://llm.example.test/v1"]));
    assert_eq!(r.post("/api/preferences", &json!({"ai_provider": "local", "ai_enabled": false})).status, 200);
    assert_eq!(ai(&r), json!([false, "local", "", ""]));

    let model = r.post("/api/preferences", &json!({"ai_model": "gpt 6"}));
    let words = "the AI model must be up to 200 letters, digits and . _ : / @ + -";
    assert_eq!((model.status, model.json()), (400, refused(words)));
    let url = r.post("/api/preferences", &json!({"ai_base_url": "http://llm.example.test/v1"}));
    assert_eq!(url.status, 400);
    assert!(url.json()["detail"].as_str().unwrap().starts_with("the AI service's URL must be https"));
    assert_eq!(r.post("/api/preferences", &json!({"ai_provider": "claude"})).status, 400);
    let elsewhere = r.post("/api/preferences", &json!({"ai_base_url": "https://llm.example.test/v1"}));
    assert_eq!(
        (elsewhere.status, elsewhere.json()),
        (400, refused("A model on this Mac must be at 127.0.0.1, ::1 or localhost."))
    );
    assert_eq!(ai(&r), json!([false, "local", "", ""]), "a refusal changes nothing");
}

// -- what the status says ------------------------------------------------------------------

#[test]
fn ai_mode_is_off_on_a_fresh_install_and_on_where_there_is_a_key_until_it_is_set() {
    let r = running(&json!({}), &[]);
    let mode = |r: &Running| r.get("/api/preferences").json()["preferences"]["ai_enabled"].clone();
    assert_eq!((mode(&r), r.ai()), (json!(false), json!(["none", "anthropic", "turned-off", "", ""])));
    r.post("/api/ai/key", &json!({"provider": "anthropic", "key": "sk-ant-fictional"}));
    assert_eq!(mode(&r), json!(true), "a key kept turns it on, unset");
    assert_eq!(r.ai(), json!(["api", "anthropic", null, "claude-opus-5-5", "mailmind.config.json"]));
    assert_eq!(r.post("/api/preferences", &json!({"ai_enabled": false})).json()["preferences"]["ai_enabled"], false);
    assert_eq!((mode(&r), r.ai()[2].clone()), (json!(false), json!("turned-off")), "set, it stands");
    let config: Value = serde_json::from_slice(&std::fs::read(r.config()).unwrap()).unwrap();
    assert_eq!(config["preferences"]["ai_enabled"], false);
    r.post("/api/ai/key/clear", &json!({"provider": "anthropic"}));
    r.post("/api/preferences", &json!({"ai_enabled": true}));
    assert_eq!((mode(&r), r.ai()[2].clone()), (json!(true), json!("no-key")));

    let r = running(&json!({}), &[("ANTHROPIC_API_KEY", "sk-ant-fictional")]);
    assert_eq!((mode(&r), r.ai()[0].clone()), (json!(true), json!("api")), "Claude's key in the environment");
    let r = running(&json!({"local_api_key": {"keychain": "local-api-key"}}), &[]);
    assert_eq!(mode(&r), json!(true), "any provider's key the file names");
}

#[test]
fn the_status_names_the_provider_why_ai_is_off_and_the_model_in_force() {
    let r = running(&json!({}), &[]);
    assert_eq!(r.ai(), json!(["none", "anthropic", "turned-off", "", ""]));
    let on = |more: Value| {
        let mut chosen = json!({"ai_enabled": true});
        chosen.as_object_mut().unwrap().extend(more.as_object().unwrap().clone());
        r.write_config(&json!({"preferences": chosen}));
        r.ai()
    };
    assert_eq!(on(json!({})), json!(["none", "anthropic", "no-key", "", ""]));
    assert_eq!(on(json!({"ai_provider": "local"})), json!(["none", "local", "no-model", "", ""]));
    assert_eq!(
        on(json!({"ai_provider": "compatible", "ai_model": "mixtral"})),
        json!(["none", "compatible", "no-url", "", ""])
    );
    assert_eq!(on(json!({"ai_provider": "gemini"})), json!(["none", "gemini", "no-key", "", ""]));
    assert_eq!(
        on(json!({"ai_provider": "local", "ai_model": "llama3.2:3b"})),
        json!(["api", "local", null, "llama3.2:3b", ""])
    );

    r.post("/api/ai/key", &json!({"provider": "openai", "key": "sk-fictional"}));
    let mut config: Value = serde_json::from_slice(&std::fs::read(r.config()).unwrap()).unwrap();
    config["preferences"] = json!({"ai_provider": "openai"});
    r.write_config(&config);
    assert_eq!(r.ai(), json!(["api", "openai", null, "gpt-6.1-sol", "mailmind.config.json"]));
    config["preferences"]["ai_enabled"] = json!(false);
    r.write_config(&config);
    let asked = *r.keychain.asked.lock().unwrap();
    assert_eq!(r.ai(), json!(["none", "openai", "turned-off", "", ""]));
    assert_eq!(*r.keychain.asked.lock().unwrap(), asked, "turned off, no key is looked up");

    let r = running(&json!({}), &[("ANTHROPIC_API_KEY", "sk-ant-fictional"), ("MAILMIND_MODEL", "claude-sonnet-5-5")]);
    assert_eq!(r.ai(), json!(["api", "anthropic", null, "claude-sonnet-5-5", "ANTHROPIC_API_KEY"]));
    r.write_config(&json!({"preferences": {"ai_provider": "local"}}));
    assert_eq!(r.ai(), json!(["api", "local", null, "claude-sonnet-5-5", ""]), "MAILMIND_MODEL wins over every choice");
    assert!(!r.get("/api/digest").text().contains("fictional"));
}

#[test]
fn the_startup_line_says_the_ai_in_force_and_never_its_url_or_key() {
    let r = running(&json!({}), &[]);
    let line = || r.app.assistant.describe_engine();
    assert_eq!(
        line(),
        "Claude (Anthropic) · claude-opus-5-5 · AI is turned off: turn it on in Settings, or with `uv run python -m \
         mailmind ai on`"
    );
    r.post("/api/preferences", &json!({"ai_enabled": true}));
    assert_eq!(
        line(),
        "Claude (Anthropic) · claude-opus-5-5 · no key, so AI is off: keep one with `uv run python -m mailmind ai \
         key`, or set ANTHROPIC_API_KEY"
    );
    r.post("/api/ai/key", &json!({"provider": "compatible", "key": "sk-fictional"}));
    let url = "https://llm.example.test/v1";
    r.post("/api/preferences", &json!({"ai_provider": "compatible", "ai_model": "mixtral", "ai_base_url": url}));
    assert_eq!(line(), "Another OpenAI-compatible service · mixtral · key from mailmind.config.json");
    r.post("/api/preferences", &json!({"ai_enabled": false}));
    assert!(line().ends_with("AI is turned off: turn it on in Settings, or with `uv run python -m mailmind ai on`"));
    assert!(!line().contains("example.test") && !line().contains("fictional"));
}

// -- a call ---------------------------------------------------------------------------------

#[test]
fn a_call_goes_to_the_provider_chosen_and_with_ai_turned_off_nowhere() {
    let (url, log) = provider(vec![(200, completion(&json!({"text": "Hi."})))]);
    let chosen = json!({"ai_enabled": true, "ai_provider": "local", "ai_model": "llama3.2:3b", "ai_base_url": url});
    let r = running(&json!({"preferences": chosen}), &[]);
    let rewrite = json!({"text": "Hello there, how are you?", "op": "shorten"});
    let answered = r.post("/api/ai/rewrite", &rewrite);
    assert_eq!((answered.status, answered.json()), (200, json!({"ok": true, "text": "Hi.", "op": "shorten"})));
    let [asked] = heard(&log).try_into().unwrap();
    assert_eq!(asked.line, "POST /v1/chat/completions HTTP/1.1");
    assert_eq!(asked.header("authorization"), None, "no key is kept for it");
    assert_eq!((asked.body["model"].clone(), asked.body["max_tokens"].clone()), (json!("llama3.2:3b"), json!(16000)));
    assert_eq!(asked.body["response_format"]["json_schema"]["strict"], true);
    let today = r.get("/api/debug/ai?days=1").json()["days"][0].clone();
    assert_eq!((today["calls"].clone(), today["cost_micros"].clone()), (json!(1), json!(0)), "{today}");

    r.post("/api/preferences", &json!({"ai_enabled": false}));
    let reached = r.outside.0.lock().unwrap().len();
    let off = r.post("/api/ai/rewrite", &rewrite);
    assert_eq!(
        (off.status, off.json()),
        (
            409,
            json!({"type": "/problems/ai-off", "title": "AI is off", "status": 409,
                     "detail": "AI is off: it is turned off in Settings", "ai_off": true})
        )
    );
    assert_eq!(r.outside.0.lock().unwrap().len(), reached, "nothing was reached");
    assert_eq!(heard(&log).len(), 1);

    // Each reason in its own words.
    for (chosen, words) in [
        (json!({"ai_enabled": true, "ai_provider": "openai"}), "AI is off: there is no API key"),
        (json!({"ai_provider": "compatible", "ai_model": "mixtral"}), "AI is off: the service has no URL"),
        (json!({"ai_provider": "local"}), "AI is off: no model is chosen"),
    ] {
        r.post("/api/preferences", &chosen);
        let off = r.post("/api/ai/rewrite", &rewrite);
        assert_eq!(
            (off.status, off.json()["detail"].clone(), off.json()["ai_off"].clone()),
            (409, json!(words), json!(true))
        );
    }
    assert_eq!(r.outside.0.lock().unwrap().len(), reached);
}

// -- the models a provider lists ---------------------------------------------------------------

#[test]
fn a_providers_models_are_listed_with_its_key_at_the_url_chosen() {
    let listed = json!({"object": "list", "data": [{"id": "zeta-2"}, {"id": "alpha-1"}, {"id": "text-embedding-3-small"},
                                                   {"id": "nomic-embed-text:latest"}, {"id": "alpha-1"}]});
    let (url, log) = provider(vec![(200, listed.clone()), (200, listed)]);
    let r =
        running(&json!({"preferences": {"ai_provider": "compatible", "ai_base_url": url, "ai_enabled": false}}), &[]);
    let models = r.get("/api/ai/models?provider=compatible");
    assert_eq!(
        (models.status, models.json()),
        (200, json!({"ok": true, "models": ["alpha-1", "zeta-2"], "default": null})),
        "with AI turned off too, once each, and none that can't chat"
    );
    r.post("/api/ai/key", &json!({"provider": "openai", "key": "sk-fictional"}));
    r.post("/api/preferences", &json!({"ai_provider": "openai", "ai_base_url": url}));
    let models = r.get("/api/ai/models?provider=openai");
    assert_eq!(models.json(), json!({"ok": true, "models": ["alpha-1", "zeta-2"], "default": "gpt-6.1-sol"}));
    let asked = heard(&log);
    let lines: Vec<&str> = asked.iter().map(|h| h.line.as_str()).collect();
    assert_eq!(lines, ["GET /v1/models HTTP/1.1", "GET /v1/models HTTP/1.1"]);
    assert_eq!(
        (asked[0].header("authorization"), asked[1].header("authorization")),
        (None, Some("Bearer sk-fictional"))
    );
    assert!(!models.text().contains("fictional"));
}

#[test]
fn what_cant_be_listed_is_refused_in_words_and_a_provider_that_didnt_answer_is_unreachable() {
    let r = running(&json!({}), &[]);
    let said = |path: &str| {
        let answer = r.get(path);
        (answer.status, answer.json()["detail"].clone())
    };
    assert_eq!(said("/api/ai/models?provider=compatible"), (400, json!("Give the service's URL first.")));
    assert_eq!(said("/api/ai/models?provider=openai"), (400, json!("Keep a key for this provider first.")));
    assert_eq!(said("/api/ai/models?provider=anthropic"), (400, json!("Keep a key for this provider first.")));
    assert_eq!(said("/api/ai/models?provider=claude"), (400, json!("bad request")));
    assert_eq!(said("/api/ai/models"), (400, json!("bad request")));

    let (url, _) = provider(vec![(401, json!({"error": {"message": "Incorrect API key: sk-fictional"}}))]);
    r.post("/api/ai/key", &json!({"provider": "gemini", "key": "sk-fictional"}));
    r.post("/api/preferences", &json!({"ai_provider": "gemini", "ai_base_url": url}));
    let refused_key = r.get("/api/ai/models?provider=gemini");
    assert_eq!((refused_key.status, refused_key.json()), (400, refused("The provider refused the key.")));

    let nowhere = format!("http://127.0.0.1:{}/v1", closed_port());
    r.post("/api/preferences", &json!({"ai_base_url": nowhere}));
    let unreached = r.get("/api/ai/models?provider=gemini");
    assert_eq!(
        (unreached.status, unreached.json()["type"].clone(), unreached.json()["detail"].clone()),
        (502, json!("/problems/unreachable"), json!("Couldn't reach the provider."))
    );
    assert!(!unreached.text().contains("fictional") && !refused_key.text().contains("fictional"));
}

// -- the background ------------------------------------------------------------------------------

#[test]
fn with_ai_turned_off_the_background_reaches_nothing() {
    let (url, log) = provider(vec![(200, completion(&json!({"summary": "A note.", "importance": 3})))]);
    let chosen = |enabled: bool| {
        json!({"preferences": {"ai_enabled": enabled, "ai_provider": "local", "ai_model": "llama3.2:3b",
                               "ai_base_url": url, "morning_brief": true, "weekly_review": true, "auto_tidy": true}})
    };
    let r = running(&chosen(false), &[("MAILMIND_PROFILE", "full")]);
    let database = Database::new(r.home.path().join("mailmind.db"), DbKey::from_bytes([0x5e; 32]), r.outside.clone());
    database
        .write(|c| {
            c.execute_batch(
                "INSERT INTO message_identity (key, account, identity) VALUES (1, 'ada@example.test', '<n1@example.test>');
                 INSERT INTO messages (account, kind, folder, uid, message_id, from_addr, subject, ts, snippet, message_key)
                 VALUES ('ada@example.test', 'imap', 'INBOX', 1, '<n1@example.test>', 'pat@example.test', 'A note',
                         1793600000, 'On the figures.', 1);",
            )
        })
        .unwrap();
    let bg = &r.app.background;
    for _ in 0..2 {
        let rounds = [bg.assess_round(), bg.draft_ahead_round(), bg.brief_ahead_round(), bg.review_ahead_round()];
        assert!(rounds.iter().all(Result::is_ok), "{rounds:?}");
        assert_eq!(bg.tidy_round(), Ok(None));
    }
    assert!(heard(&log).is_empty(), "turned off: nothing was asked");
    assert!(r.outside.0.lock().unwrap().is_empty(), "nor anything reached");

    r.write_config(&chosen(true));
    assert!(bg.assess_round().is_ok());
    let asked = heard(&log);
    assert_eq!(asked.len(), 1, "on, the same round asks the provider chosen");
    assert_eq!(asked[0].line, "POST /v1/chat/completions HTTP/1.1");
}
