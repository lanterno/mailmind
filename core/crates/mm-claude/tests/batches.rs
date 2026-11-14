//! The Message Batches API's calls against a stand-in on loopback (`tests/unit/assistant/test_ai_batches.py`'s
//! adapter half): what a batch carries, that sending one is never retried, how one is asked about, read and removed, and
//! that the key goes nowhere but the endpoint it was given for.

use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use mm_assistant::ports::{LanguageModel, Prompt};
use mm_claude::ClaudeApi;
use mm_claude::batches::segment;
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

/// A request as the stand-in read it: its request line, its headers (names lower-cased) and its body.
#[derive(Clone, Debug)]
struct Heard {
    request_line: String,
    headers: Vec<(String, String)>,
    body: Vec<u8>,
}

impl Heard {
    fn header(&self, name: &str) -> Option<&str> {
        self.headers.iter().find(|(n, _)| n == name).map(|(_, v)| v.as_str())
    }
    fn json(&self) -> Value {
        serde_json::from_slice(&self.body).unwrap_or(Value::Null)
    }
}

/// A status and a body the stand-in answers one request with.
type Answer = (u16, String);

/// A stand-in that answers each connection with the next of `answers` and keeps what it was sent.
fn stand_in(answers: Vec<Answer>) -> (String, Arc<Mutex<Vec<Heard>>>) {
    let listener = TcpListener::bind("127.0.0.1:0").expect("a loopback port");
    let url = format!("http://127.0.0.1:{}", listener.local_addr().expect("its address").port());
    let heard = Arc::new(Mutex::new(Vec::new()));
    let kept = heard.clone();
    std::thread::spawn(move || {
        for (status, body) in answers {
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
            let sent = read[head_end + 4..].to_vec();
            kept.lock().unwrap_or_else(std::sync::PoisonError::into_inner).push(Heard {
                request_line,
                headers,
                body: sent,
            });
            let answer = format!(
                "HTTP/1.1 {status} X\r\ncontent-type: application/json\r\nretry-after-ms: 1\r\ncontent-length: {}\r\n\r\n{body}",
                body.len()
            );
            let _ = stream.write_all(answer.as_bytes());
        }
    });
    (url, heard)
}

fn heard(log: &Arc<Mutex<Vec<Heard>>>) -> Vec<Heard> {
    log.lock().unwrap_or_else(std::sync::PoisonError::into_inner).clone()
}

fn api(url: &str) -> ClaudeApi {
    ClaudeApi::new(Secret::new(KEY.into()), Some(url), Arc::new(Open::default())).with_limits(Duration::from_secs(5), 2)
}

fn prompt(text: &str) -> Prompt {
    Prompt {
        text: text.into(),
        model: "claude-opus-5-5".into(),
        max_tokens: 16000,
        schema: Some(json!({"type": "object", "properties": {"summary": {"type": "string"}}})),
        effort: Some("low".into()),
        fallback: true,
        system: Some("Be brief."),
    }
}

/// The batch as the API describes it, `ended` or still in progress, with `results_url` once it has one.
fn view(id: &str, ended: bool, results_url: Option<&str>) -> String {
    json!({"id": id, "type": "message_batch", "processing_status": if ended { "ended" } else { "in_progress" },
           "results_url": results_url})
    .to_string()
}

fn error(kind: &str) -> String {
    json!({"type": "error", "error": {"type": kind, "message": kind}}).to_string()
}

fn message(text: &str, stop: &str) -> Value {
    json!({"type": "message", "model": "claude-opus-5-5", "stop_reason": stop,
           "content": [{"type": "text", "text": text}], "usage": {"input_tokens": 1000, "output_tokens": 100}})
}

#[test]
fn a_batch_carries_each_request_and_never_the_fallback() {
    let (url, log) = stand_in(vec![(200, view("msgbatch_01", false, None))]);
    let id = api(&url).submit_batch(&[("11".into(), prompt("first")), ("12".into(), prompt("second"))]).expect("sent");
    assert_eq!(id, "msgbatch_01");
    let [req] = heard(&log).try_into().expect("one request");
    assert_eq!(req.request_line, "POST /v1/messages/batches HTTP/1.1");
    assert_eq!((req.header("x-api-key"), req.header("anthropic-version")), (Some(KEY), Some("2026-10-21")));
    assert_eq!(req.header("content-type"), Some("application/json"));
    assert!(req.header("anthropic-beta").is_none(), "no fallback beta for a batch");
    let sent = req.json();
    let requests = sent["requests"].as_array().expect("its requests");
    assert_eq!(requests.iter().map(|r| r["custom_id"].clone()).collect::<Vec<_>>(), vec![json!("11"), json!("12")]);
    let params = &requests[0]["params"];
    assert_eq!(params["messages"], json!([{"role": "user", "content": "first"}]));
    assert_eq!(
        (params["system"].as_str(), params["output_config"]["effort"].as_str()),
        (Some("Be brief."), Some("low"))
    );
    assert!(params.get("fallbacks").is_none(), "batches reject the fallback");
}

#[test]
fn sending_a_batch_is_never_retried() {
    let (url, log) = stand_in(vec![(500, error("api_error")), (200, view("msgbatch_02", false, None))]);
    let failed = api(&url).submit_batch(&[("1".into(), prompt("x"))]).expect_err("not sent");
    assert_eq!((failed.status, failed.permanent()), (Some(500), false));
    assert_eq!(heard(&log).len(), 1, "another try could pay for it twice");
}

#[test]
fn an_answer_with_no_id_is_no_batch() {
    let (url, _) = stand_in(vec![(200, json!({"type": "message_batch"}).to_string())]);
    let failed = api(&url).submit_batch(&[("1".into(), prompt("x"))]).expect_err("no id");
    assert_eq!(failed.status, None);
}

#[test]
fn a_batch_has_ended_when_the_api_says_so_and_asking_is_sent_again_after_an_overload() {
    let (url, log) = stand_in(vec![
        (200, view("msgbatch_03", false, None)),
        (529, error("overloaded_error")),
        (200, view("msgbatch_03", true, Some("https://api.example.test/r"))),
    ]);
    let client = api(&url);
    assert_eq!(client.batch_ended("msgbatch_03"), Ok(false));
    assert_eq!(client.batch_ended("msgbatch_03"), Ok(true));
    let asked = heard(&log);
    assert_eq!(asked.len(), 3);
    assert_eq!(asked[0].request_line, "GET /v1/messages/batches/msgbatch_03 HTTP/1.1");
    assert_eq!((asked[0].header("accept"), asked[0].header("x-api-key")), (Some("application/json"), Some(KEY)));
    assert!(asked[0].header("content-length").is_none() && asked[0].header("content-type").is_none());
    assert!(asked[0].body.is_empty());
}

#[test]
fn results_are_read_as_json_lines_from_the_endpoint_the_key_is_for() {
    let lines = [
        json!({"custom_id": "1", "result": {"type": "succeeded", "message": message("{\"summary\": \"fine\"}", "end_turn")}}),
        json!({"custom_id": "2", "result": {"type": "errored", "error": {"type": "error",
               "error": {"type": "invalid_request_error", "message": "bad"}}}}),
        json!({"custom_id": "3", "result": {"type": "errored", "error": {"type": "error",
               "error": {"type": "overloaded_error", "message": "busy"}}}}),
        json!({"custom_id": "4", "result": {"type": "expired"}}),
        json!({"custom_id": "5", "result": {"type": "canceled"}}),
        json!({"custom_id": "6", "result": {"type": "succeeded", "message": message("", "refusal")}}),
    ];
    let body = format!(
        "{}\r\n\n{}\n",
        lines[..3].iter().map(Value::to_string).collect::<Vec<_>>().join("\n"),
        lines[3..].iter().map(Value::to_string).collect::<Vec<_>>().join("\n")
    );
    // The batch names its results somewhere else: they are still read here, and the key goes nowhere else.
    let (url, log) =
        stand_in(vec![(200, view("msgbatch_04", true, Some("https://results.example.test/elsewhere"))), (200, body)]);
    let outside = Arc::new(Open::default());
    let client = ClaudeApi::new(Secret::new(KEY.into()), Some(&url), outside.clone());
    let got = client.batch_results("msgbatch_04", "claude-opus-5-5").expect("its results");
    let asked = heard(&log);
    assert_eq!(asked[1].request_line, "GET /v1/messages/batches/msgbatch_04/results HTTP/1.1");
    assert_eq!(asked[1].header("accept"), Some("application/binary"));
    assert_eq!(*outside.reached.lock().expect("hosts"), vec!["127.0.0.1".to_owned(); 2]);
    let ids: Vec<&str> = got.iter().map(|r| r.custom_id.as_str()).collect();
    assert_eq!(ids, ["1", "2", "3", "4", "5", "6"]);
    let reply = got[0].reply.as_ref().expect("an answer");
    assert_eq!((reply.text.as_str(), reply.usage.input_tokens), ("{\"summary\": \"fine\"}", 1000));
    assert_eq!((got[1].reply.is_none(), got[1].permanent), (true, true), "a bad request is permanent");
    for other in &got[2..5] {
        assert_eq!((other.reply.is_none(), other.permanent), (true, false), "{}", other.custom_id);
    }
    let refused = got[5].reply.as_ref().expect("a refusal is a reply");
    assert!(refused.refused() && refused.text.is_empty());
}

#[test]
fn a_batch_with_no_results_yet_has_none_to_read() {
    let (url, log) = stand_in(vec![(200, view("msgbatch_05", false, None))]);
    let failed = api(&url).batch_results("msgbatch_05", "claude-opus-5-5").expect_err("not yet");
    assert_eq!(failed.status, None);
    assert_eq!(heard(&log).len(), 1, "nothing is asked for the results");
}

#[test]
fn a_line_that_isnt_json_fails_the_reading() {
    let (url, _) = stand_in(vec![
        (200, view("msgbatch_06", true, Some("http://127.0.0.1/r"))),
        (200, "{\"custom_id\": \"1\", \"result\": {\"type\": \"expired\"}}\nnot json\n".into()),
    ]);
    let failed = api(&url).batch_results("msgbatch_06", "claude-opus-5-5").expect_err("unreadable");
    assert_eq!(failed.status, None);
}

#[test]
fn a_batch_is_removed_and_one_that_is_gone_answers_404() {
    let (url, log) = stand_in(vec![
        (200, json!({"id": "msgbatch_07", "type": "message_batch_deleted"}).to_string()),
        (404, error("not_found_error")),
    ]);
    let client = api(&url);
    client.discard_batch("msgbatch_07").expect("removed");
    assert_eq!(heard(&log)[0].request_line, "DELETE /v1/messages/batches/msgbatch_07 HTTP/1.1");
    let gone = client.batch_ended("msgbatch_07").expect_err("gone");
    assert_eq!((gone.status, gone.permanent()), (Some(404), true));
}

#[test]
fn an_id_goes_into_the_path_as_one_segment_and_one_naming_another_path_is_never_sent() {
    assert_eq!(segment("msgbatch_01AbC-x.y~z").as_deref(), Some("msgbatch_01AbC-x.y~z"));
    assert_eq!(segment("a/../b c\r\n").as_deref(), Some("a%2F..%2Fb%20c%0D%0A"));
    for refused in ["", ".", ".."] {
        assert_eq!(segment(refused), None, "{refused:?}");
    }
    let (url, log) = stand_in(vec![(200, view("x", true, None))]);
    let client = api(&url);
    for id in ["", ".."] {
        assert_eq!(client.batch_ended(id).expect_err("refused").status, None);
        assert_eq!(client.discard_batch(id).expect_err("refused").status, None);
    }
    assert!(heard(&log).is_empty());
    client.batch_ended("a/b").expect("asked");
    assert_eq!(heard(&log)[0].request_line, "GET /v1/messages/batches/a%2Fb HTTP/1.1");
}

#[test]
fn the_key_goes_in_the_clear_only_to_this_machine() {
    let outside = Arc::new(Open::default());
    let client = ClaudeApi::new(Secret::new(KEY.into()), Some("http://api.example.test"), outside.clone());
    assert_eq!(client.submit_batch(&[("1".into(), prompt("x"))]).expect_err("refused").status, None);
    assert_eq!(client.batch_ended("msgbatch_08").expect_err("refused").status, None);
    assert_eq!(client.batch_results("msgbatch_08", "claude-opus-5-5").expect_err("refused").status, None);
    assert_eq!(client.discard_batch("msgbatch_08").expect_err("refused").status, None);
    assert!(outside.reached.lock().expect("hosts").is_empty(), "nothing was reached");
}
