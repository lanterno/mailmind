//! OpenAI's Chat Completions client against a stand-in on loopback (#49): what a request carries for each provider,
//! how replies, refusals and usage come back, structured output's fallback, when a call is sent again, and that the
//! key never goes in the clear off this machine or into an error.

use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use mm_assistant::ports::{LanguageModel, ModelError, Prompt};
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
fn gemini_takes_max_tokens_this_mac_and_a_compatible_service_no_ceiling_and_any_with_a_key_sends_it() {
    for (provider, key, bearer, ceiling) in [
        (Gemini, Some(KEY), Some("Bearer sk-proj-test-fake-key"), Some(16000)),
        (Local, Some(KEY), Some("Bearer sk-proj-test-fake-key"), None),
        (Local, None, None, None),
        (Compatible, None, None, None),
        (Compatible, Some(""), None, None),
        (Compatible, Some(KEY), Some("Bearer sk-proj-test-fake-key"), None),
    ] {
        let (url, log) = stand_in(vec![(200, vec![], answer("Hi.", "m"))]);
        api(provider, &url, key, Arc::new(Open::default())).complete(&prompt("m", None)).expect("a reply");
        let [req] = heard(&log).try_into().expect("one request");
        assert_eq!(req.header("authorization"), bearer, "{provider:?}");
        assert_eq!(req.body.get("max_tokens").and_then(Value::as_u64), ceiling, "{provider:?}");
        assert!(req.body.get("max_completion_tokens").is_none() && req.body.get("response_format").is_none());
    }
}

#[test]
fn openai_and_gemini_are_asked_once_more_without_a_ceiling_they_refuse() {
    for (provider, field, said) in [
        (
            OpenAi,
            "max_completion_tokens",
            "max_completion_tokens is too large: 16000. This model supports at most 4096.",
        ),
        (OpenAi, "max_completion_tokens", "Unsupported parameter: 'max_tokens' is not supported with this model."),
        (Gemini, "max_tokens", "Invalid value for max_tokens: must be at most 8192."),
    ] {
        let (url, log) = stand_in(vec![(400, vec![], error(said)), (200, vec![], answer("{\"text\": \"hi\"}", "m"))]);
        let reply = api(provider, &url, Some(KEY), Arc::new(Open::default()))
            .complete(&prompt("m", Some(closed())))
            .expect("a reply");
        assert_eq!(reply.text, "{\"text\": \"hi\"}");
        let asked = heard(&log);
        assert_eq!(asked.len(), 2, "{provider:?}");
        assert_eq!(asked[0].body[field], 16000, "{provider:?}");
        let mut again = asked[1].body.clone();
        assert!(again.as_object_mut().expect("a body").remove(field).is_none(), "{provider:?}: no ceiling again");
        let mut first = asked[0].body.clone();
        first.as_object_mut().expect("a body").remove(field);
        assert_eq!(again, first, "{provider:?}: all else as it was, structured output too");
    }
    // Once a call: a second refusal is the answer, and another 400 is the request's fault.
    let refused = || (400, vec![], error("max_tokens is too large"));
    let (url, log) = stand_in(vec![refused(), refused(), (200, vec![], answer("never", "m"))]);
    let failed = api(Gemini, &url, Some(KEY), Arc::new(Open::default())).complete(&prompt("m", None)).expect_err("400");
    assert_eq!((failed.status, heard(&log).len()), (Some(400), 2));
    let (url, log) = stand_in(vec![(400, vec![], error("model is required")), (200, vec![], answer("never", "m"))]);
    let failed = api(OpenAi, &url, Some(KEY), Arc::new(Open::default())).complete(&prompt("m", None)).expect_err("400");
    assert_eq!((failed.status, heard(&log).len()), (Some(400), 1));
    // Words that name it anywhere but in what the service said are no refusal of it.
    let (url, log) = stand_in(vec![(400, vec![], json!({"max_tokens": 1, "error": "bad"}).to_string())]);
    api(OpenAi, &url, Some(KEY), Arc::new(Open::default())).complete(&prompt("m", None)).expect_err("400");
    assert_eq!(heard(&log).len(), 1);
    // A service sent no ceiling isn't asked again for one it names.
    let (url, log) =
        stand_in(vec![(400, vec![], error("max_tokens must be set")), (200, vec![], answer("never", "m"))]);
    api(Compatible, &url, None, Arc::new(Open::default())).complete(&prompt("m", None)).expect_err("400");
    assert_eq!(heard(&log).len(), 1);
}

#[test]
fn a_body_leaves_out_each_thing_a_service_refused_and_each_is_left_out_once_a_call() {
    use mm_kernel::ai_provider::AiProvider;
    use mm_openai::wire::{Without, body};
    // No provider is sent both structured output and a ceiling it may refuse, so the two together are the wire's.
    let asked = prompt("m", Some(closed()));
    let both = body(&asked, AiProvider::Anthropic, Without { format: true, ceiling: true });
    assert!(both.get("max_tokens").is_none() && both.get("response_format").is_none());
    let neither = body(&asked, AiProvider::Gemini, Without::default());
    assert!(neither.get("max_tokens").is_some() && neither.get("response_format").is_some());
    let format_only = body(&asked, AiProvider::Gemini, Without { format: true, ceiling: false });
    assert_eq!((format_only.get("max_tokens"), format_only.get("response_format")), (Some(&json!(16000)), None));
    // Refused again without it, structured output isn't asked without a third time.
    let refusal = || (400, vec![], error("unknown field: response_format"));
    let (url, log) = stand_in(vec![refusal(), refusal(), (200, vec![], answer("never", "m"))]);
    let failed = api(Local, &url, None, Arc::new(Open::default())).complete(&asked).expect_err("400");
    assert_eq!((failed.status, heard(&log).len()), (Some(400), 2));
}

#[test]
fn a_key_refused_with_a_400_is_never_asked_again_without_structured_output_or_a_ceiling() {
    // As a 401 is never asked again: the same refused key would go once more.
    let not_valid = |also: &str| {
        json!({"error": {"code": 400, "message": format!("API key not valid. {also}"),
                         "details": [{"reason": "API_KEY_INVALID"}]}})
        .to_string()
    };
    let by_reason = |also: &str| {
        json!([{"error": {"message": format!("Bad request: {also}"), "details": [{"reason": "API_KEY_INVALID"}]}}])
            .to_string()
    };
    for (provider, body) in [
        (Compatible, not_valid("response_format is unsupported")),
        (Local, by_reason("unknown field: response_format")),
        (Gemini, not_valid("max_tokens is too large")),
        (OpenAi, by_reason("max_completion_tokens is too large")),
        (Gemini, json!({"error": "API key not valid; max_tokens is too large"}).to_string()),
    ] {
        let (url, log) = stand_in(vec![(400, vec![], body.clone()), (200, vec![], answer("never", "m"))]);
        let failed = api(provider, &url, Some(KEY), Arc::new(Open::default()))
            .complete(&prompt("m", Some(closed())))
            .expect_err("refused");
        assert_eq!(
            failed,
            mm_assistant::ports::ModelError::new(format!("{} API 401: the key was refused", provider.id()), Some(401))
        );
        assert_eq!(heard(&log).len(), 1, "{provider:?}: {body}");
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
fn the_output_is_whatever_total_tokens_says_beyond_the_prompt_where_that_is_more() {
    let usage = |usage: Value| {
        json!({"model": "gemini-4-hydrogen", "choices": [{"message": {"role": "assistant", "content": "Hi."},
               "finish_reason": "stop"}], "usage": usage})
        .to_string()
    };
    let (url, _) = stand_in(vec![
        // Gemini's thinking: in the total, not in the completion's count.
        (200, vec![], usage(json!({"prompt_tokens": 3000, "completion_tokens": 200, "total_tokens": 4800}))),
        (200, vec![], usage(json!({"prompt_tokens": 3000, "completion_tokens": 200, "total_tokens": 3200}))),
        (200, vec![], usage(json!({"prompt_tokens": 3000, "completion_tokens": 200, "total_tokens": 3100}))),
        (200, vec![], usage(json!({"prompt_tokens": 3000, "completion_tokens": 200}))),
        (
            200,
            vec![],
            usage(json!({"prompt_tokens": 3000, "completion_tokens": 200, "total_tokens": 4800,
                      "prompt_tokens_details": {"cached_tokens": 1000}})),
        ),
    ]);
    let client = api(Gemini, &url, Some(KEY), Arc::new(Open::default()));
    let mut counted = Vec::new();
    for _ in 0..5 {
        let usage = client.complete(&prompt("gemini-4-hydrogen", None)).expect("a reply").usage;
        counted.push((usage.input_tokens, usage.cache_read_tokens, usage.output_tokens));
    }
    assert_eq!(counted, [(3000, 0, 1800), (3000, 0, 200), (3000, 0, 200), (3000, 0, 200), (2000, 1000, 1800)]);
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
        // Asked again, it is told the shape in words: the schema as compact JSON, its keys in their own order.
        let stated = "Say hello.\n\nReply with only a JSON object that matches this JSON Schema: \
                      {\"type\":\"object\",\"properties\":{\"text\":{\"type\":\"string\"}},\"required\":[\"text\"],\
                      \"additionalProperties\":false}";
        assert_eq!(
            asked[1].body["messages"],
            json!([{"role": "system", "content": "Be brief."}, {"role": "user", "content": stated}]),
            "{provider:?}"
        );
        assert_eq!(asked[0].body["messages"][1]["content"], "Say hello.", "the first time, only the prompt");
    }
    // With no schema there is nothing to fall back from, nor to state.
    let (url, log) = stand_in(vec![(400, vec![], error("unknown field: response_format"))]);
    let failed = api(Local, &url, None, Arc::new(Open::default())).complete(&prompt("m", None)).expect_err("400");
    assert_eq!((failed.status, heard(&log).len()), (Some(400), 1));
    assert_eq!(heard(&log)[0].body["messages"][1]["content"], "Say hello.");
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
fn a_key_with_a_space_or_a_line_break_in_it_is_a_refused_key_and_never_sent() {
    for (provider, spaced) in [
        (OpenAi, "sk-proj-one\nsk-proj-two"),
        (Gemini, "gemini key"),
        (Local, "sk-local\r\n"),
        (Compatible, "sk\u{7f}compatible"),
        (Compatible, "sk\u{a0}compatible"),
        // Nor any character outside ASCII: none can go in a header.
        (OpenAi, "sk-proj-t\u{e9}st"),
        (Gemini, "sk-gemini\u{200b}"),
        (Local, "\u{feff}sk-local"),
        (Compatible, "sk-\u{201c}compatible\u{201d}"),
    ] {
        let (url, log) = stand_in(vec![(200, vec![], answer("never", "m"))]);
        let outside = Arc::new(Open::default());
        let client = api(provider, &url, Some(spaced), outside.clone());
        let failed = client.complete(&prompt("m", None)).expect_err("refused");
        assert_eq!((failed.status, failed.permanent()), (Some(401), true), "{provider:?}");
        assert_eq!(
            failed,
            mm_assistant::ports::ModelError::new(format!("{} API 401: the key was refused", provider.id()), Some(401))
        );
        assert_eq!(failed.plain(), "the API key was rejected");
        assert_eq!(client.list_models().expect_err("refused").status, Some(401), "{provider:?}");
        assert!(heard(&log).is_empty() && outside.reached.lock().expect("hosts").is_empty(), "{provider:?}");
        assert!(!format!("{failed:?}").contains("sk"));
    }
}

/// Gemini's answer to a wrong key: a 400, its error in a list, by its reason, its message or both.
fn gemini_wrong_key(reason: Option<&str>, message: &str) -> String {
    let details = reason.map_or_else(Vec::new, |reason| {
        vec![json!({"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason,
                    "domain": "googleapis.com"})]
    });
    json!([{"error": {"code": 400, "message": message, "status": "INVALID_ARGUMENT", "details": details}}]).to_string()
}

#[test]
fn geminis_400_for_a_key_that_isnt_valid_is_a_refused_key_as_a_401_is() {
    let not_valid = "API key not valid. Please pass a valid API key.";
    for body in [
        gemini_wrong_key(Some("API_KEY_INVALID"), not_valid),
        gemini_wrong_key(Some("API_KEY_INVALID"), &format!("Bad key {KEY}")),
        gemini_wrong_key(None, not_valid),
        // The same, not in a list.
        json!({"error": {"code": 400, "message": not_valid, "status": "INVALID_ARGUMENT"}}).to_string(),
        // An error that is words is what the service said, alone or the first of a list.
        json!({"error": not_valid}).to_string(),
        json!([{"error": not_valid}, {"error": "second"}]).to_string(),
    ] {
        let (url, log) = stand_in(vec![(400, vec![], body.clone())]);
        let failed = api(Gemini, &url, Some(KEY), Arc::new(Open::default()))
            .complete(&prompt("gemini-4-hydrogen", None))
            .expect_err("refused");
        assert_eq!((failed.status, failed.permanent(), heard(&log).len()), (Some(401), true, 1), "{body}");
        assert_eq!(
            failed,
            mm_assistant::ports::ModelError::new("gemini API 401: the key was refused", Some(401)),
            "{body}"
        );
        assert_eq!(failed.plain(), "the API key was rejected", "{body}");
        assert!(!format!("{failed:?}").contains("sk-proj"));
        // The models it lists: a refused key too.
        let (url, _) = stand_in(vec![(400, vec![], body)]);
        let listed = api(Gemini, &url, Some(KEY), Arc::new(Open::default())).list_models().expect_err("refused");
        assert_eq!(listed.status, Some(401));
    }
    // Another 400 is the request's fault, whatever its error says of a key in passing.
    for body in [
        gemini_wrong_key(Some("FIELD_INVALID"), "Invalid JSON payload received. Unknown name \"seed\"."),
        gemini_wrong_key(None, "The API key not valid for this model is fine; the field isn't."),
        error("model is required"),
        json!({"error": " API key not valid"}).to_string(),
        // Words that aren't what the service said: the second of a list, or beside the error.
        json!([{"error": "bad request"}, {"error": "API key not valid"}]).to_string(),
        json!({"message": "API key not valid", "error": "bad request"}).to_string(),
    ] {
        let (url, _) = stand_in(vec![(400, vec![], body.clone())]);
        let failed = api(Gemini, &url, Some(KEY), Arc::new(Open::default()))
            .complete(&prompt("gemini-4-hydrogen", None))
            .expect_err("a bad request");
        assert_eq!((failed.status, failed.plain().as_str()), (Some(400), "the API answered 400"), "{body}");
    }
    // A 401 or a 403 refuses the key, whatever its body is.
    let (url, _) = stand_in(vec![(403, vec![], gemini_wrong_key(Some("API_KEY_INVALID"), not_valid))]);
    let failed = api(Gemini, &url, Some(KEY), Arc::new(Open::default())).complete(&prompt("m", None)).expect_err("403");
    assert_eq!(failed.status, Some(403));
}

#[test]
fn what_a_service_said_is_read_from_the_first_of_a_list_too() {
    use mm_openai::wire::error_text;
    let listed = json!([{"error": {"code": 429, "message": "Resource has been exhausted."}}, {"error": "second"}]);
    assert_eq!(error_text(listed.to_string().as_bytes()), "Resource has been exhausted.");
    assert_eq!(error_text(br#"{"error": {"message": "slow down"}}"#), "slow down");
    assert_eq!(error_text(br#"{"error": "model not found"}"#), "model not found");
    assert_eq!(error_text(br#"[{"error": "model not found"}]"#), "model not found");
    for none in [&b"[]"[..], b"[1]", b"not json", b"{}", b"[{\"message\": \"no error\"}]"] {
        assert_eq!(error_text(none), "", "{}", String::from_utf8_lossy(none));
    }
    // Read, it says what a person can be told: a rate limit, for one.
    let (url, _) =
        stand_in(vec![(400, vec![], json!([{"error": {"message": "rate of requests too high"}}]).to_string())]);
    let failed = api(Gemini, &url, Some(KEY), Arc::new(Open::default())).complete(&prompt("m", None)).expect_err("400");
    assert_eq!(failed.plain(), "rate-limited for now");
}

#[test]
fn the_key_is_taken_out_of_all_a_service_said_before_it_is_cut_short() {
    use mm_openai::wire::{SAID_MOST, said};
    // The key starts before the cut and runs past it.
    let lead = "x".repeat(SAID_MOST - 45);
    let quoted = error(&format!("{lead} invalid header Authorization: Bearer {KEY} and more after it"));
    let kept = said(quoted.as_bytes(), Some(KEY));
    assert_eq!(kept.chars().count(), SAID_MOST);
    assert!(!kept.contains("sk-proj") && kept.ends_with("Bearer <key> a"), "{kept}");
    let lead = "x".repeat(SAID_MOST - 10);
    let whole = format!("{lead} <key> and more");
    assert_eq!(said(error(&format!("{lead} {KEY} and more")).as_bytes(), Some(KEY)), whole[..SAID_MOST]);
    // Every place it is said, and nothing to take out with no key.
    assert_eq!(said(error(&format!("{KEY}, {KEY}")).as_bytes(), Some(KEY)), "<key>, <key>");
    assert_eq!(said(error("no key here").as_bytes(), None), "no key here");
    assert_eq!(said(error("no key here").as_bytes(), Some("")), "no key here");
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

/// A service that answers each request with a head saying a long body, then a blank every fifth of a second for ten
/// seconds: never idle a second, never done in time.
fn trickling() -> String {
    let listener = TcpListener::bind("127.0.0.1:0").expect("a loopback port");
    let url = format!("http://127.0.0.1:{}/v1", listener.local_addr().expect("its address").port());
    std::thread::spawn(move || {
        for mut stream in listener.incoming().flatten() {
            std::thread::spawn(move || {
                let mut chunk = [0u8; 4096];
                let _ = stream.read(&mut chunk);
                let _ = stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n");
                for _ in 0..50 {
                    std::thread::sleep(Duration::from_millis(200));
                    if stream.write_all(b" ").is_err() {
                        return;
                    }
                }
            });
        }
    });
    url
}

#[test]
fn an_attempt_is_held_to_its_time_limit_all_told_however_the_answer_trickles_in() {
    let url = trickling();
    let client = ChatApi::new(Compatible, &url, None, Arc::new(Open::default())).with_limits(Duration::from_secs(1), 0);
    let began = std::time::Instant::now();
    let timed_out = ModelError::new("compatible unreachable: timed out", None);
    assert_eq!(client.list_models().expect_err("timed out"), timed_out);
    assert!(began.elapsed() < Duration::from_millis(2500), "{:?}", began.elapsed());
    let began = std::time::Instant::now();
    assert_eq!(client.complete(&prompt("m", None)).expect_err("timed out"), timed_out);
    assert!(began.elapsed() < Duration::from_millis(2500), "{:?}", began.elapsed());
}

#[test]
fn an_answer_that_cant_be_read_is_a_failure() {
    let (url, _) = stand_in(vec![(200, vec![], "not json".into())]);
    let failed = api(OpenAi, &url, Some(KEY), Arc::new(Open::default())).complete(&prompt("m", None)).expect_err("bad");
    assert_eq!(failed.status, None);
}

#[test]
fn a_reasoning_models_leading_thinking_is_dropped_before_the_reply_is_read() {
    let thought = "<think>\nThe person wants it short. {\"text\": \"no\"}\n</think>\n\n{\"text\": \"Hi.\"}";
    let thinking = "  <thinking>Short, then.</thinking>{\"text\": \"Hello.\"}";
    let (url, _) = stand_in(vec![(200, vec![], answer(thought, "qwen3:8b")), (200, vec![], answer(thinking, "m"))]);
    for (provider, said) in [(Local, "{\"text\": \"Hi.\"}"), (Compatible, "{\"text\": \"Hello.\"}")] {
        let reply = api(provider, &url, None, Arc::new(Open::default()))
            .complete(&prompt("qwen3:8b", Some(closed())))
            .expect("a reply");
        assert_eq!(reply.text, said, "{provider:?}");
    }
}

#[test]
fn only_a_leading_closed_thinking_block_is_dropped() {
    use mm_openai::wire::without_thinking;
    assert_eq!(without_thinking("<think>a</think>b"), "b");
    assert_eq!(without_thinking("\n <thinking>\na\n</thinking> b"), " b");
    assert_eq!(without_thinking("<think>a</think>b<think>c</think>"), "b<think>c</think>", "the first block only");
    assert_eq!(without_thinking("b <think>a</think>"), "b <think>a</think>", "not first");
    assert_eq!(without_thinking("<think>never closed"), "<think>never closed");
    assert_eq!(without_thinking("<think>a</thinking>b"), "<think>a</thinking>b", "a block closes as it opened");
    assert_eq!(without_thinking("<THINK>a</THINK>b"), "<THINK>a</THINK>b");
    assert_eq!(without_thinking("{\"text\": \"Hi.\"}"), "{\"text\": \"Hi.\"}");
}

#[test]
fn only_a_list_of_models_is_readable_as_one() {
    let page = "<html>a web page</html>".to_owned();
    let (url, _) =
        stand_in(vec![(200, vec![], page.clone()), (200, vec![], page), (200, vec![], "{\"data\": []}".into())]);
    let api = api(Local, &url, None, Arc::new(Open::default()));
    assert_eq!(api.list_models().expect("read as none"), Vec::<String>::new());
    let failed = api.readable_models().expect_err("not a list");
    assert_eq!(failed.status, None);
    assert_eq!(api.readable_models().expect("an empty list"), Vec::<String>::new());
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
