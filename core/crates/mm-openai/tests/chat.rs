//! OpenAI's Chat Completions client against a stand-in on loopback (#49): what a request carries for each provider,
//! how replies, refusals and usage come back, structured output's fallback, when a call is sent again, and that the
//! key never goes in the clear off this machine or into an error.

use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use mm_assistant::ports::{LanguageModel, Prompt};
use mm_kernel::ai_provider::AiProvider::{self, Compatible, Gemini, Local, OpenAi};
use mm_kernel::seams::{Outside, Secret, Tripped};
use mm_openai::ChatApi;
use mm_openai::wire::strict;
use serde_json::{Value, json};

const KEY: &str = "sk-proj-test-fake-key";

/// Lets everything through, and remembers the hosts it was asked about.
#[derive(Debug, Default)]
struct Open {
    reached: Mutex<Vec<String>>,
}

impl Outside for Open {
    fn open(&self, _path: &Path) -> Result<(), Tripped> {
        Ok(())
    }
    fn reach(&self, host: &str) -> Result<(), Tripped> {
        self.reached.lock().unwrap_or_else(std::sync::PoisonError::into_inner).push(host.to_owned());
        Ok(())
    }
    fn run(&self, _program: &str) -> Result<(), Tripped> {
        Ok(())
    }
}

/// Refuses every host.
#[derive(Debug)]
struct Closed;

impl Outside for Closed {
    fn open(&self, _path: &Path) -> Result<(), Tripped> {
        Ok(())
    }
    fn reach(&self, host: &str) -> Result<(), Tripped> {
        Err(Tripped(host.to_owned()))
    }
    fn run(&self, _program: &str) -> Result<(), Tripped> {
        Ok(())
    }
}

/// A request as the stand-in read it: its request line, lower-cased header names, and its JSON body.
#[derive(Clone, Debug)]
struct Heard {
    request_line: String,
    headers: Vec<(String, String)>,
    body: Value,
}

impl Heard {
    fn header(&self, name: &str) -> Option<&str> {
        self.headers.iter().find(|(n, _)| n == name).map(|(_, v)| v.as_str())
    }
}

/// What the stand-in answers one request with: a status, headers beside the content type, and a body.
type Answer = (u16, Vec<(&'static str, &'static str)>, String);

/// A stand-in at `/v1` that answers each connection with the next of `answers` and keeps what it was sent.
fn stand_in(answers: Vec<Answer>) -> (String, Arc<Mutex<Vec<Heard>>>) {
    let listener = TcpListener::bind("127.0.0.1:0").expect("a loopback port");
    let url = format!("http://127.0.0.1:{}/v1/", listener.local_addr().expect("its address").port());
    let heard = Arc::new(Mutex::new(Vec::new()));
    let kept = heard.clone();
    std::thread::spawn(move || {
        for (status, extra, body) in answers {
            let Ok((mut stream, _)) = listener.accept() else { return };
            let mut read = Vec::new();
            let mut chunk = [0u8; 4096];
            let (head_end, length) = loop {
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
            while read.len() < head_end + 4 + length {
                let n = stream.read(&mut chunk).unwrap_or(0);
                if n == 0 {
                    break;
                }
                read.extend_from_slice(&chunk[..n]);
            }
            let head = String::from_utf8_lossy(&read[..head_end]).into_owned();
            let mut lines = head.split("\r\n");
            let request_line = lines.next().unwrap_or_default().to_owned();
            let headers = lines
                .filter_map(|l| l.split_once(':'))
                .map(|(n, v)| (n.trim().to_lowercase(), v.trim().to_owned()))
                .collect();
            let sent = serde_json::from_slice(&read[head_end + 4..]).unwrap_or(Value::Null);
            kept.lock().unwrap_or_else(std::sync::PoisonError::into_inner).push(Heard {
                request_line,
                headers,
                body: sent,
            });
            let mut answer = format!("HTTP/1.0 {status} X\r\ncontent-type: application/json\r\n");
            for (name, value) in extra {
                answer.push_str(&format!("{name}: {value}\r\n"));
            }
            answer.push_str(&format!("content-length: {}\r\n\r\n{body}", body.len()));
            let _ = stream.write_all(answer.as_bytes());
        }
    });
    (url, heard)
}

fn heard(log: &Arc<Mutex<Vec<Heard>>>) -> Vec<Heard> {
    log.lock().unwrap_or_else(std::sync::PoisonError::into_inner).clone()
}

/// A completion that says `text`, served by `model`.
fn answer(text: &str, model: &str) -> String {
    json!({"id": "chatcmpl-1", "object": "chat.completion", "model": model,
           "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
           "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}})
    .to_string()
}

fn error(message: &str) -> String {
    json!({"error": {"message": message, "type": "invalid_request_error"}}).to_string()
}

fn closed() -> Value {
    json!({"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
           "additionalProperties": false})
}

fn prompt(model: &str, schema: Option<Value>) -> Prompt {
    Prompt {
        text: "Say hello.".into(),
        model: model.into(),
        max_tokens: 16000,
        schema,
        effort: Some("medium".into()),
        fallback: true,
        system: Some("Be brief."),
    }
}

fn api(provider: AiProvider, url: &str, key: Option<&str>, outside: Arc<dyn Outside>) -> ChatApi {
    ChatApi::new(provider, url, key.map(|k| Secret::new(k.into())), outside).with_limits(Duration::from_secs(5), 2)
}

#[test]
fn a_request_carries_the_key_the_system_prompt_the_prompt_and_its_schema_strict() {
    let (url, log) = stand_in(vec![(200, vec![], answer("  Hello.  ", "gpt-6.1-sol-2026-10-01"))]);
    let outside = Arc::new(Open::default());
    let reply = api(OpenAi, &url, Some(KEY), outside.clone())
        .complete(&prompt("gpt-6.1-sol", Some(closed())))
        .expect("a reply");
    assert_eq!(
        (reply.text.as_str(), reply.model.as_str(), reply.stop_reason.as_str(), reply.refused()),
        ("Hello.", "gpt-6.1-sol-2026-10-01", "stop", false)
    );
    let [req] = heard(&log).try_into().expect("one request");
    assert_eq!(req.request_line, "POST /v1/chat/completions HTTP/1.1");
    assert_eq!(req.header("authorization"), Some("Bearer sk-proj-test-fake-key"));
    assert_eq!(req.header("content-type"), Some("application/json"));
    assert!(req.headers.iter().all(|(name, _)| !name.starts_with("anthropic-") && name != "x-api-key"));
    assert_eq!(
        req.body,
        json!({"model": "gpt-6.1-sol",
               "messages": [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "Say hello."}],
               "max_completion_tokens": 16000,
               "response_format": {"type": "json_schema",
                                   "json_schema": {"name": "answer", "schema": closed(), "strict": true}}}),
        "no effort, no fallback"
    );
    assert_eq!(*outside.reached.lock().expect("hosts"), vec!["127.0.0.1".to_owned()]);
}

#[test]
fn a_schema_is_strict_only_with_every_object_closed_and_every_property_required() {
    assert!(strict(&closed()));
    assert!(strict(&json!({"type": "object", "properties": {"items": {"type": "array", "items": closed()}},
                           "required": ["items"], "additionalProperties": false})));
    let optional = json!({"type": "object", "properties": {"text": {"type": "string"}, "note": {"type": "string"}},
                          "required": ["text"], "additionalProperties": false});
    assert!(!strict(&optional));
    let open = json!({"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]});
    assert!(!strict(&open));
    let nested = json!({"type": "object", "properties": {"inner": open}, "required": ["inner"],
                        "additionalProperties": false});
    assert!(!strict(&nested));
    assert!(!strict(&json!({"anyOf": [closed(), {"type": "object"}]})));
    assert!(strict(&json!({"type": "string"})));

    let (url, log) = stand_in(vec![(200, vec![], answer("{}", "gpt-6.1-sol"))]);
    api(OpenAi, &url, Some(KEY), Arc::new(Open::default())).complete(&prompt("gpt-6.1-sol", Some(optional))).ok();
    assert_eq!(heard(&log)[0].body["response_format"]["json_schema"]["strict"], false);
}

#[test]
fn every_provider_but_openai_takes_max_tokens_and_any_with_a_key_sends_it() {
    for (provider, key, bearer) in [
        (Gemini, Some(KEY), Some("Bearer sk-proj-test-fake-key")),
        (Local, Some(KEY), Some("Bearer sk-proj-test-fake-key")),
        (Local, None, None),
        (Compatible, None, None),
        (Compatible, Some(""), None),
        (Compatible, Some(KEY), Some("Bearer sk-proj-test-fake-key")),
    ] {
        let (url, log) = stand_in(vec![(200, vec![], answer("Hi.", "m"))]);
        api(provider, &url, key, Arc::new(Open::default())).complete(&prompt("m", None)).expect("a reply");
        let [req] = heard(&log).try_into().expect("one request");
        assert_eq!(req.header("authorization"), bearer, "{provider:?}");
        assert_eq!(req.body["max_tokens"], 16000, "{provider:?}");
        assert!(req.body.get("max_completion_tokens").is_none() && req.body.get("response_format").is_none());
    }
}

#[test]
fn a_refusal_reads_as_an_empty_reply_and_cached_tokens_as_cache_reads() {
    let refused = json!({"model": "gpt-6.1-sol", "choices": [{"message": {"role": "assistant", "content": null,
                         "refusal": "I can't help with that."}, "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 1000, "completion_tokens": 5}})
    .to_string();
    let cached = json!({"model": "gemini-4-hydrogen", "choices": [{"message": {"role": "assistant", "content": [
                        {"type": "text", "text": "Part one, "}, {"type": "image_url"}, {"type": "text", "text": "two."}]},
                        "finish_reason": "length"}],
                        "usage": {"prompt_tokens": 1000, "completion_tokens": 50,
                                  "prompt_tokens_details": {"cached_tokens": 400}}})
    .to_string();
    let (url, _) = stand_in(vec![(200, vec![], refused), (200, vec![], cached)]);
    let client = api(Gemini, &url, Some(KEY), Arc::new(Open::default()));
    let reply = client.complete(&prompt("gpt-6.1-sol", Some(closed()))).expect("a refusal");
    assert!(reply.refused() && reply.text.is_empty());
    assert_eq!((reply.usage.input_tokens, reply.usage.output_tokens), (1000, 5));
    let reply = client.complete(&prompt("gemini-4-hydrogen", None)).expect("a reply");
    assert_eq!((reply.text.as_str(), reply.stop_reason.as_str()), ("Part one, two.", "length"));
    let usage = reply.usage;
    assert_eq!(
        (usage.input_tokens, usage.cache_read_tokens, usage.output_tokens, usage.cache_write_tokens),
        (600, 400, 50, 0)
    );
}

#[test]
fn a_service_that_refuses_structured_output_is_asked_once_more_without_it() {
    for provider in [Local, Compatible] {
        let refusal = error("unknown field: response_format");
        let (url, log) = stand_in(vec![(400, vec![], refusal), (200, vec![], answer("{\"text\": \"hi\"}", "m"))]);
        let reply = api(provider, &url, None, Arc::new(Open::default()))
            .complete(&prompt("llama3.2:3b", Some(closed())))
            .expect("a reply");
        assert_eq!(reply.text, "{\"text\": \"hi\"}");
        let asked = heard(&log);
        assert_eq!(asked.len(), 2, "{provider:?}");
        assert!(asked[0].body.get("response_format").is_some() && asked[1].body.get("response_format").is_none());
    }
    // OpenAI's own 400 is the request's fault, and another 400 is no refusal of structured output.
    for (provider, said) in [(OpenAi, "unknown field: response_format"), (Local, "model is required")] {
        let (url, log) = stand_in(vec![(400, vec![], error(said)), (200, vec![], answer("never", "m"))]);
        let failed = api(provider, &url, Some(KEY), Arc::new(Open::default()))
            .complete(&prompt("m", Some(closed())))
            .expect_err("a bad request");
        assert_eq!((failed.status, failed.permanent(), heard(&log).len()), (Some(400), true, 1), "{provider:?}");
    }
}

#[test]
fn a_rejected_key_says_nothing_the_service_said_and_is_not_sent_again() {
    for status in [401, 403] {
        let echoed = error(&format!("Incorrect API key provided: {KEY}"));
        let (url, log) = stand_in(vec![(status, vec![], echoed)]);
        let failed = api(OpenAi, &url, Some(KEY), Arc::new(Open::default()))
            .complete(&prompt("gpt-6.1-sol", None))
            .expect_err("refused");
        assert_eq!((failed.status, failed.permanent(), heard(&log).len()), (Some(status), true, 1));
        assert!(!format!("{failed:?}").contains("sk-proj"));
        assert!(!failed.plain().contains("sk-proj"));
    }
    let (url, _) = stand_in(vec![(401, vec![], error("no"))]);
    let failed = api(OpenAi, &url, Some(KEY), Arc::new(Open::default())).complete(&prompt("m", None)).expect_err("401");
    assert_eq!(failed.plain(), "the API key was rejected");
}

#[test]
fn a_rate_limit_is_sent_again_and_an_outage_that_stays_gives_up_after_two_retries() {
    let (url, log) = stand_in(vec![
        (429, vec![("retry-after-ms", "1")], error("slow down")),
        (200, vec![], answer("Second time.", "gpt-6.1-sol")),
    ]);
    let reply = api(OpenAi, &url, Some(KEY), Arc::new(Open::default()))
        .complete(&prompt("gpt-6.1-sol", None))
        .expect("a reply");
    assert_eq!((reply.text.as_str(), heard(&log).len()), ("Second time.", 2));

    let busy = || (503, vec![("retry-after-ms", "1")], error("busy"));
    let (url, log) = stand_in(vec![busy(), busy(), busy()]);
    let failed = api(Gemini, &url, Some(KEY), Arc::new(Open::default()))
        .complete(&prompt("gemini-4-hydrogen", None))
        .expect_err("busy");
    assert_eq!((failed.status, failed.permanent(), heard(&log).len()), (Some(503), false, 3));
    assert_eq!(failed.plain(), "the API answered 503");
}

#[test]
fn the_key_goes_in_the_clear_only_to_this_machine_and_a_refused_host_is_never_reached() {
    let outside = Arc::new(Open::default());
    let failed = api(Compatible, "http://llm.example.test/v1", Some(KEY), outside.clone())
        .complete(&prompt("m", None))
        .expect_err("plain HTTP off this machine");
    assert_eq!((failed.status, failed.plain().as_str()), (None, "couldn't reach it"));
    assert!(outside.reached.lock().expect("hosts").is_empty(), "nothing was reached");

    let (url, log) = stand_in(vec![(200, vec![], answer("never", "m"))]);
    let failed = api(Local, &url, None, Arc::new(Closed)).complete(&prompt("m", None)).expect_err("refused");
    assert_eq!((failed.status, failed.plain().as_str()), (None, "couldn't reach it"));
    assert!(heard(&log).is_empty());
}

#[test]
fn an_answer_that_cant_be_read_is_a_failure() {
    let (url, _) = stand_in(vec![(200, vec![], "not json".into())]);
    let failed = api(OpenAi, &url, Some(KEY), Arc::new(Open::default())).complete(&prompt("m", None)).expect_err("bad");
    assert_eq!(failed.status, None);
}

#[test]
fn a_services_models_are_listed_once_with_its_key() {
    let listed = json!({"object": "list", "data": [{"id": "gpt-6.1-sol", "object": "model"}, {"id": "whisper-1"},
                        {"object": "model"}]})
    .to_string();
    let (url, log) = stand_in(vec![(200, vec![], listed)]);
    let ids = api(OpenAi, &url, Some(KEY), Arc::new(Open::default())).list_models().expect("a list");
    assert_eq!(ids, ["gpt-6.1-sol", "whisper-1"]);
    let [req] = heard(&log).try_into().expect("one request");
    assert_eq!(req.request_line, "GET /v1/models HTTP/1.1");
    assert_eq!(req.header("authorization"), Some("Bearer sk-proj-test-fake-key"));

    let (url, log) = stand_in(vec![(503, vec![("retry-after-ms", "1")], error("busy"))]);
    let failed = api(Local, &url, None, Arc::new(Open::default())).list_models().expect_err("busy");
    assert_eq!((failed.status, heard(&log).len()), (Some(503), 1), "a person is waiting: asked once");
    let (url, _) = stand_in(vec![(401, vec![], error("no"))]);
    let failed = api(Gemini, &url, Some(KEY), Arc::new(Open::default())).list_models().expect_err("refused");
    assert_eq!(failed.status, Some(401));
}
