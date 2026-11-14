//! The Claude API's client against a stand-in on loopback: what a request carries, how replies and errors come back,
//! when a call is sent again, and that the key never goes in the clear off this machine.

use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use mm_assistant::ports::{LanguageModel, Prompt};
use mm_claude::{ClaudeApi, Endpoint};
use mm_kernel::seams::{Outside, Secret, Tripped};
use serde_json::{Value, json};

const KEY: &str = "sk-ant-test-fake-key";

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

/// A request as the stand-in read it: its head, lower-cased names, and its JSON body.
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

/// A stand-in that answers each connection with the next of `answers` (status, extra headers, body) and keeps what
/// it was sent.
/// What the stand-in answers one request with: a status, headers beside the content type, and a body.
type Answer = (u16, Vec<(&'static str, &'static str)>, String);

fn stand_in(answers: Vec<Answer>) -> (String, Arc<Mutex<Vec<Heard>>>) {
    let listener = TcpListener::bind("127.0.0.1:0").expect("a loopback port");
    let url = format!("http://127.0.0.1:{}", listener.local_addr().expect("its address").port());
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

fn answer(text: &str) -> String {
    json!({"type": "message", "model": "claude-opus-5-5", "stop_reason": "end_turn",
           "content": [{"type": "text", "text": text}], "usage": {"input_tokens": 12, "output_tokens": 3}})
    .to_string()
}

fn error(kind: &str, message: &str) -> String {
    json!({"type": "error", "error": {"type": kind, "message": message}}).to_string()
}

fn prompt(model: &str, fallback: bool) -> Prompt {
    Prompt {
        text: "Say hello.".into(),
        model: model.into(),
        max_tokens: 16000,
        schema: Some(json!({"type": "object", "properties": {"text": {"type": "string"}}})),
        effort: Some("medium".into()),
        fallback,
        system: Some("Be brief."),
    }
}

fn api(url: &str, outside: Arc<dyn Outside>) -> ClaudeApi {
    ClaudeApi::new(Secret::new(KEY.into()), Some(url), outside).with_limits(Duration::from_secs(5), 2)
}

fn heard(log: &Arc<Mutex<Vec<Heard>>>) -> Vec<Heard> {
    log.lock().unwrap_or_else(std::sync::PoisonError::into_inner).clone()
}

#[test]
fn a_request_carries_the_key_the_version_the_prompt_and_the_refusal_fallback() {
    let (url, log) = stand_in(vec![(200, vec![], answer("  Hello.  "))]);
    let outside = Arc::new(Open::default());
    let reply = api(&url, outside.clone()).complete(&prompt("claude-opus-5-5", true)).expect("a reply");
    assert_eq!(
        (reply.text.as_str(), reply.model.as_str(), reply.stop_reason.as_str()),
        ("Hello.", "claude-opus-5-5", "end_turn")
    );
    assert_eq!((reply.usage.input_tokens, reply.usage.output_tokens), (12, 3));
    let [req] = heard(&log).try_into().expect("one request");
    assert_eq!(req.request_line, "POST /v1/messages HTTP/1.1");
    assert_eq!(req.header("x-api-key"), Some(KEY));
    assert_eq!(req.header("anthropic-version"), Some("2026-10-21"));
    assert_eq!(req.header("anthropic-beta"), Some("server-side-fallback-2026-07-01"));
    assert_eq!(req.body["fallbacks"], "default");
    assert_eq!(req.body["system"], "Be brief.");
    assert_eq!(req.body["messages"], json!([{"role": "user", "content": "Say hello."}]));
    assert_eq!(req.body["output_config"]["effort"], "medium");
    assert_eq!(req.body["output_config"]["format"]["type"], "json_schema");
    assert_eq!(*outside.reached.lock().expect("hosts"), vec!["127.0.0.1".to_owned()]);
}

#[test]
fn a_model_without_the_fallback_asks_for_none() {
    let (url, log) = stand_in(vec![(200, vec![], answer("Hi."))]);
    api(&url, Arc::new(Open::default())).complete(&prompt("claude-haiku-5-5", false)).expect("a reply");
    let [req] = heard(&log).try_into().expect("one request");
    assert!(req.header("anthropic-beta").is_none() && req.body.get("fallbacks").is_none());
}

#[test]
fn a_rate_limit_is_sent_again_and_a_rejected_key_is_not() {
    let (url, log) = stand_in(vec![
        (429, vec![("retry-after-ms", "1")], error("rate_limit_error", "slow down")),
        (200, vec![], answer("Second time.")),
    ]);
    let reply = api(&url, Arc::new(Open::default())).complete(&prompt("claude-opus-5-5", true)).expect("a reply");
    assert_eq!((reply.text.as_str(), heard(&log).len()), ("Second time.", 2));

    let (url, log) = stand_in(vec![(401, vec![], error("authentication_error", "invalid x-api-key"))]);
    let failed = api(&url, Arc::new(Open::default())).complete(&prompt("claude-opus-5-5", true)).expect_err("refused");
    assert_eq!(
        (failed.status, failed.permanent(), failed.plain().as_str()),
        (Some(401), true, "the API key was rejected")
    );
    assert!(!format!("{failed:?}").contains("sk-ant"));
    assert_eq!(heard(&log).len(), 1);
}

#[test]
fn an_overload_that_stays_gives_up_after_two_retries() {
    let busy = || (529, vec![("retry-after-ms", "1")], error("overloaded_error", "busy"));
    let (url, log) = stand_in(vec![busy(), busy(), busy()]);
    let failed = api(&url, Arc::new(Open::default())).complete(&prompt("claude-opus-5-5", true)).expect_err("busy");
    assert_eq!((failed.status, failed.permanent(), heard(&log).len()), (Some(529), false, 3));
    assert_eq!(failed.plain(), "the API answered 529");
}

#[test]
fn a_bad_request_says_its_status() {
    let (url, _) = stand_in(vec![(400, vec![], error("invalid_request_error", "bad request"))]);
    let failed = api(&url, Arc::new(Open::default())).complete(&prompt("claude-opus-5-5", true)).expect_err("bad");
    assert_eq!((failed.status, failed.permanent(), failed.plain().as_str()), (Some(400), true, "the API answered 400"));
}

#[test]
fn a_refusal_reads_as_an_empty_reply_and_only_the_text_after_a_fallback_counts() {
    let refused = json!({"stop_reason": "refusal", "content": [], "model": "claude-opus-5-5"}).to_string();
    let handed = json!({"model": "claude-opus-5-5", "stop_reason": "end_turn", "content": [
        {"type": "text", "text": "half"}, {"type": "fallback", "to": {"model": "claude-opus-4-8"}},
        {"type": "text", "text": "whole"}]})
    .to_string();
    let (url, _) = stand_in(vec![(200, vec![], refused), (200, vec![], handed)]);
    let client = api(&url, Arc::new(Open::default()));
    let reply = client.complete(&prompt("claude-opus-5-5", true)).expect("a refusal");
    assert!(reply.refused() && reply.text.is_empty());
    let reply = client.complete(&prompt("claude-opus-5-5", true)).expect("a reply");
    assert_eq!((reply.text.as_str(), reply.model.as_str()), ("whole", "claude-opus-4-8"));
}

#[test]
fn the_key_goes_in_the_clear_only_to_this_machine() {
    let outside = Arc::new(Open::default());
    let failed = api("http://api.example.test", outside.clone())
        .complete(&prompt("claude-opus-5-5", true))
        .expect_err("plain HTTP off this machine");
    assert_eq!(failed.status, None);
    assert!(outside.reached.lock().expect("hosts").is_empty(), "nothing was reached");
}

#[test]
fn a_host_the_tripwire_refuses_is_never_reached() {
    let (url, log) = stand_in(vec![(200, vec![], answer("never"))]);
    let failed = api(&url, Arc::new(Closed)).complete(&prompt("claude-opus-5-5", true)).expect_err("refused");
    assert_eq!((failed.status, failed.plain().as_str()), (None, "ModelError"));
    assert!(heard(&log).is_empty());
}

#[test]
fn an_endpoint_is_read_from_its_url() {
    let parsed = Endpoint::parse("https://api.anthropic.com").expect("the API's own");
    assert_eq!(
        (parsed.tls, parsed.host.as_str(), parsed.port, parsed.base.as_str()),
        (true, "api.anthropic.com", 443, "")
    );
    let fake = Endpoint::parse("http://127.0.0.1:8123/proxy/").expect("a stand-in's");
    assert_eq!((fake.tls, fake.port, fake.base.as_str(), fake.loopback()), (false, 8123, "/proxy", true));
    assert!(Endpoint::parse("http://[::1]:9/").expect("IPv6").loopback());
    assert!(!Endpoint::parse("http://localhost.example.test").expect("a name").loopback());
    assert_eq!(Endpoint::parse("ftp://api.example.test"), None);
}

#[test]
fn a_url_with_a_line_break_or_a_blank_inside_it_is_no_endpoint() {
    for url in [
        "https://api.example.test\r\nX-Api-Key: other",
        "https://api.example.test/v\n1",
        "http://127.0.0.1:8123/a b",
        "https://api.example.test\t/",
        "https://api.example.test\u{0}",
    ] {
        assert_eq!(Endpoint::parse(url), None, "{url:?}");
    }
    assert!(Endpoint::parse("  https://api.example.test/  ").is_some(), "blanks around it are trimmed");
}

#[test]
fn a_chunked_reply_is_read_whole() {
    let sent = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n4;ext=1\r\n{\"a\"\r\n3\r\n:1}\r\n0\r\n\r\n";
    let read = mm_claude::http::read_response(&mut &sent[..]).expect("a whole reply");
    assert_eq!((read.status, read.body.as_slice()), (200, &b"{\"a\":1}"[..]));
}

#[test]
fn a_chunked_reply_cut_short_is_a_lost_connection() {
    let sent = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n8\r\n{\"a\"";
    assert_eq!(mm_claude::http::read_response(&mut &sent[..]), Err(mm_net::Lost("ConnectionLost")));
}

#[test]
fn a_chunk_larger_than_any_reply_is_refused_before_it_is_waited_for() {
    for size in ["ffffffffffffffff", "2000001"] {
        let sent = format!("HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n{size}\r\nx");
        let read = mm_claude::http::read_response(&mut sent.as_bytes());
        assert_eq!(read, Err(mm_net::Lost("ResponseTooLarge")), "{size}");
    }
    let sent = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nzz\r\nx\r\n0\r\n\r\n";
    assert_eq!(mm_claude::http::read_response(&mut &sent[..]), Err(mm_net::Lost("ProtocolError")));
}
