//! A thread's or a message's summary, asked of Claude on a person's click (`application.thread_digest`), and kept in
//! `thread_summaries` under a fingerprint of the thread, so a new reply makes it stale. One run at a time for each
//! (account, thread, scope), so a double click never pays twice. The mail goes to the model as untrusted data, within
//! the caps.

use std::collections::BTreeSet;
use std::sync::Arc;
use std::time::Duration;

use mm_kernel::errors::Problem;
use mm_kernel::seams::Clock;
use serde_json::{Map, Value, json};

use crate::calls::Calls;
use crate::context::{Context, lone_key};
use crate::digest::{self, Digest};
use crate::flights::SingleFlight;
use crate::ports::{Answers, StoredSummary};
use crate::prompts;
use crate::refusals::{AiFailure, answered};
use crate::text::cut;

/// How long a second request waits on the first's summary.
const WAIT: Duration = Duration::from_secs(60);

/// What a request asks to have summarized: a message by its pk, or a thread by its account and id.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Asked {
    pub pk: Option<i64>,
    pub account: Option<String>,
    pub thread_id: Option<String>,
    pub force: bool,
}

#[derive(Debug)]
pub struct ThreadDigest {
    calls: Arc<Calls>,
    answers: Arc<dyn Answers>,
    context: Context,
    clock: Arc<dyn Clock>,
    flights: SingleFlight<(String, String, String)>,
}

fn json_list(text: Option<&str>) -> Value {
    match text.filter(|t| !t.is_empty()).map(serde_json::from_str::<Value>) {
        Some(Ok(found)) => found,
        _ => json!([]),
    }
}

/// A stored summary as the page reads it, after what says whose it is.
fn expanded(head: Map<String, Value>, row: &StoredSummary) -> Value {
    let mut out = head;
    out.insert("tldr".into(), json!(row.tldr));
    out.insert("points".into(), json_list(row.points.as_deref()));
    out.insert("action".into(), json!(row.action));
    out.insert("participants".into(), json_list(row.participants.as_deref()));
    out.insert("message_count".into(), json!(row.message_count));
    out.insert("model".into(), json!(row.model));
    out.insert("created_at".into(), json!(row.created_at));
    Value::Object(out)
}

fn head(account: &str, thread_id: &str, scope: &str, cached: bool, stale: bool) -> Map<String, Value> {
    let mut out = Map::new();
    out.insert("ok".into(), json!(true));
    out.insert("account".into(), json!(account));
    out.insert("thread_id".into(), json!(thread_id));
    out.insert("scope".into(), json!(scope));
    out.insert("cached".into(), json!(cached));
    out.insert("stale".into(), json!(stale));
    out
}

impl ThreadDigest {
    pub fn new(calls: Arc<Calls>, answers: Arc<dyn Answers>, context: Context, clock: Arc<dyn Clock>) -> ThreadDigest {
        ThreadDigest { calls, answers, context, clock, flights: SingleFlight::default() }
    }

    /// `Asking.summarize`: the summary a request asks for, or None while another request is still writing it. A
    /// message names its own account and thread; an account the app doesn't use, or no thread, is refused.
    pub fn asked(&self, asked: &Asked) -> Result<Option<Value>, AiFailure> {
        let mut account = asked.account.clone();
        let mut thread_id = asked.thread_id.clone();
        let mut scope = "thread";
        if let Some(pk) = asked.pk {
            let row = self.context.mail().message(pk)?.ok_or_else(|| Problem::not_found("not found"))?;
            thread_id = Some(match row.thread_id.as_deref().filter(|t| !t.is_empty()) {
                Some(tid) => tid.to_owned(),
                None => {
                    scope = "message";
                    lone_key(row.message_id.as_deref(), row.pk)
                }
            });
            account = Some(row.account);
        }
        let accounts = self.context.accounts()?;
        let (Some(account), Some(thread_id)) =
            (account.filter(|a| !a.is_empty() && accounts.contains(a)), thread_id.filter(|t| !t.is_empty()))
        else {
            return Err(Problem::bad_request().into());
        };
        self.summarize(&account, &thread_id, scope, asked.force)
    }

    fn fingerprint(rows: &[crate::ports::Said]) -> String {
        let newest = rows.iter().map(|r| r.ts.unwrap_or(0)).max().unwrap_or(0);
        digest::fingerprint(rows.iter().map(|r| r.message_id.as_deref()), newest)
    }

    /// The thread's summary, or None when another request is still writing it past the wait.
    pub fn summarize(
        &self,
        account: &str,
        thread_id: &str,
        scope: &str,
        force: bool,
    ) -> Result<Option<Value>, AiFailure> {
        let rows = self.context.rows(account, thread_id)?;
        if rows.is_empty() {
            return Err(Problem::not_found("nothing to summarize").into());
        }
        let fp = Self::fingerprint(&rows);
        if !force && let Some(cached) = self.answers.thread_summary(account, thread_id, scope)? {
            let stale = cached.fingerprint.as_deref() != Some(fp.as_str());
            return Ok(Some(expanded(head(account, thread_id, scope, true, stale), &cached)));
        }
        let read = || -> Option<Result<Value, AiFailure>> {
            match self.answers.thread_summary(account, thread_id, scope) {
                Ok(Some(row)) if row.fingerprint.as_deref() == Some(fp.as_str()) => {
                    Some(Ok(expanded(head(account, thread_id, scope, true, false), &row)))
                }
                Ok(_) => None,
                Err(fault) => Some(Err(fault.into())),
            }
        };
        let make = || self.make(account, thread_id, scope, &rows, &fp);
        let key = (account.to_owned(), thread_id.to_owned(), scope.to_owned());
        self.flights.run(key, make, read, WAIT, !force).transpose()
    }

    fn make(
        &self,
        account: &str,
        thread_id: &str,
        scope: &str,
        rows: &[crate::ports::Said],
        fp: &str,
    ) -> Result<Value, AiFailure> {
        let (blocks, omitted) = self.context.gather(rows)?;
        let subject = rows[0].subject.as_deref().filter(|s| !s.is_empty()).unwrap_or("(no subject)").to_owned();
        let kind = if scope == "message" { "MESSAGE" } else { "THREAD" };
        let omit =
            if omitted > 0 { prompts::THREAD_OMITTED.replace("{n}", &omitted.to_string()) } else { String::new() };
        let prompt = prompts::THREAD.fill(&[
            ("kind", kind),
            ("label", prompts::thread_label(kind)),
            ("subject", &subject),
            ("n", &rows.len().to_string()),
            ("omit", &omit),
            ("blocks", &blocks.join("\n")),
        ]);
        let out = answered(self.calls.call_claude(&prompt, "thread"), "claude failed", "summary unavailable")?;
        let (mut parsed, model) = match digest::parse(&out) {
            Some(parsed) => (parsed, self.calls.model_label()),
            // Replied, but not with JSON: a safe fallback is kept, so it isn't asked again and again.
            None => {
                let last = rows[rows.len() - 1].snippet.as_deref().unwrap_or_default();
                let fallback = Digest {
                    tldr: subject.clone(),
                    points: vec![cut(last, 200).to_owned()],
                    action: String::new(),
                    participants: vec![],
                };
                (fallback, "parse-error".to_owned())
            }
        };
        if parsed.participants.is_empty() {
            let named: BTreeSet<String> = rows
                .iter()
                .filter(|r| r.from_addr.as_deref().is_some_and(|a| !a.is_empty()))
                .map(|r| {
                    r.from_name.clone().filter(|n| !n.is_empty()).or_else(|| r.from_addr.clone()).unwrap_or_default()
                })
                .collect();
            parsed.participants = named.into_iter().take(12).collect();
        }
        let created_at = mm_kernel::when::iso(self.clock.now());
        let stored = StoredSummary {
            account: account.to_owned(),
            thread_id: thread_id.to_owned(),
            scope: scope.to_owned(),
            fingerprint: Some(fp.to_owned()),
            tldr: Some(parsed.tldr.clone()),
            points: Some(python_json(&json!(parsed.points))),
            action: Some(parsed.action.clone()),
            participants: Some(python_json(&json!(parsed.participants))),
            message_count: i64::try_from(rows.len()).ok(),
            model: Some(model.clone()),
            created_at: Some(created_at),
        };
        self.answers.save_thread_summary(&stored)?;
        let mut answer = head(account, thread_id, scope, false, false);
        answer.insert("tldr".into(), json!(parsed.tldr));
        answer.insert("points".into(), json!(parsed.points));
        answer.insert("action".into(), json!(parsed.action));
        answer.insert("participants".into(), json!(parsed.participants));
        answer.insert("message_count".into(), json!(rows.len()));
        answer.insert("model".into(), json!(model));
        answer.insert("created_at".into(), json!(stored.created_at));
        Ok(Value::Object(answer))
    }
}

/// A list of text as Python's `json.dumps` writes it: `, ` between items, everything past ASCII escaped.
pub fn python_json(value: &Value) -> String {
    match value {
        Value::Array(items) => format!("[{}]", items.iter().map(python_json).collect::<Vec<_>>().join(", ")),
        Value::String(s) => {
            let mut out = String::from("\"");
            for c in s.chars() {
                match c {
                    '"' => out.push_str("\\\""),
                    '\\' => out.push_str("\\\\"),
                    '\n' => out.push_str("\\n"),
                    '\r' => out.push_str("\\r"),
                    '\t' => out.push_str("\\t"),
                    '\u{8}' => out.push_str("\\b"),
                    '\u{c}' => out.push_str("\\f"),
                    c if (' '..='~').contains(&c) => out.push(c),
                    c => {
                        let mut units = [0u16; 2];
                        for unit in c.encode_utf16(&mut units) {
                            out.push_str(&format!("\\u{unit:04x}"));
                        }
                    }
                }
            }
            out.push('"');
            out
        }
        other => other.to_string(),
    }
}
