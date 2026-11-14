//! Ask your inbox (`application.ask` and `domain.citations`): a question about the person's own mail, answered from
//! what full-text search finds. A bounded slice of each message's text (its body, else the triage's summary, else its
//! snippet) goes to Claude under a schema, and the model answers in prose with the pks it used. Its citations are kept
//! only where it was shown that message, so it can never cite one outside the account it was asked about. Read-only:
//! no IMAP, nothing changed.

use std::collections::HashSet;
use std::sync::Arc;

use mm_kernel::errors::Problem;
use mm_kernel::seams::Clock;
use serde_json::{Map, Value, json};

use crate::calls::Calls;
use crate::context::Context;
use crate::ports::{AskIndex, AskRow};
use crate::prompts;
use crate::refusals::{AiFailure, answered, unreachable};
use crate::text::{cut, length, python_int, python_str, strip, truthy};

pub const MAX_QUESTION: usize = 8000;
/// Rows full-text search gives before the texts are chosen.
pub const CAND_LIMIT: i64 = 40;
/// Messages whose text goes in the prompt.
pub const READ_MAX: usize = 14;
pub const CAP_PER_MSG: usize = 1200;
pub const CAP_TOTAL: usize = 14000;

/// Words that say nothing to search by.
const STOP: [&str; 58] = [
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "did", "do", "does", "for", "from", "had", "has", "have",
    "how", "i", "in", "is", "it", "its", "me", "my", "of", "on", "or", "that", "the", "their", "them", "they", "this",
    "to", "was", "we", "were", "what", "when", "where", "which", "who", "whom", "why", "will", "with", "you", "your",
    "about", "any", "can", "could", "would", "should", "there", "these", "those",
];
const STOP_TOO: [&str; 1] = ["am"];

pub fn schema() -> Value {
    json!({"type": "object", "properties": {"answer": {"type": "string"},
           "cited_pks": {"type": "array", "items": {"type": "integer"}}}, "required": ["answer"]})
}

/// Python's `\w`, and the two more a term keeps at its ends.
fn kept(c: char) -> bool {
    c.is_alphanumeric() || c == '_' || c == '@' || c == '.'
}

/// A question's content words, each quoted for FTS5, OR-joined: "" when it has none.
pub fn fts_match(question: &str) -> String {
    let mut out: Vec<String> = Vec::new();
    for word in question.split(mm_kernel::text::is_space).filter(|w| !w.is_empty()) {
        let term = word.trim_matches(|c: char| !kept(c));
        let lower = term.to_lowercase();
        if length(term) >= 2 && !STOP.contains(&lower.as_str()) && !STOP_TOO.contains(&lower.as_str()) && out.len() < 12
        {
            out.push(format!("\"{}\"", term.replace('"', "\"\"")));
        }
    }
    out.join(" OR ")
}

/// Ask's citations: each message the reply named and was shown, once, in the order it named them.
pub fn cited(told: Option<&Value>, shown: &HashSet<i64>) -> Vec<i64> {
    let Some(Value::Array(named)) = told else { return Vec::new() };
    let mut out = Vec::new();
    for pk in named.iter().filter_map(python_int) {
        if shown.contains(&pk) && !out.contains(&pk) {
            out.push(pk);
        }
    }
    out
}

#[derive(Debug)]
pub struct Asker {
    calls: Arc<Calls>,
    index: Arc<dyn AskIndex>,
    context: Context,
    clock: Arc<dyn Clock>,
}

impl Asker {
    pub fn new(calls: Arc<Calls>, index: Arc<dyn AskIndex>, context: Context, clock: Arc<dyn Clock>) -> Asker {
        Asker { calls, index, context, clock }
    }

    fn answer(&self, account: &str, question: &str, answer: &str, sources: Vec<Value>) -> Value {
        let mut out = Map::new();
        out.insert("ok".into(), json!(true));
        out.insert("account".into(), json!(account));
        out.insert("question".into(), json!(question));
        out.insert("answer".into(), json!(answer));
        out.insert("sources".into(), Value::Array(sources));
        out.insert("model".into(), json!(self.calls.model_label()));
        out.insert("generated".into(), json!(mm_kernel::when::iso(self.clock.now())));
        Value::Object(out)
    }

    /// `POST /api/ai/ask`: the account is checked before any mail is read; "all" asks across every one.
    pub fn ask(&self, account: Option<&str>, question: Option<&str>) -> Result<Value, AiFailure> {
        let account = strip(account.filter(|a| !a.is_empty()).unwrap_or("all"));
        let account = if account.is_empty() { "all" } else { account };
        let question = cut(strip(question.unwrap_or_default()), MAX_QUESTION).to_owned();
        let accounts = self.context.accounts()?;
        if account != "all" && !accounts.iter().any(|a| a == account) {
            return Err(Problem::bad_request().into());
        }
        if question.is_empty() {
            return Err(Problem::refused("question required").into());
        }
        let scope = if account == "all" { accounts } else { vec![account.to_owned()] };
        let found = self
            .index
            .for_asking(&fts_match(&question), &scope, CAND_LIMIT)?
            .ok_or_else(|| Problem::refused("bad query"))?;
        // A message counts once, however many folders hold it.
        let mut once = HashSet::new();
        let rows: Vec<AskRow> = found.into_iter().filter(|r| once.insert(r.message_key.unwrap_or(-r.pk))).collect();
        if rows.is_empty() {
            let none = "I couldn\u{2019}t find any messages matching that in your mail.";
            return Ok(self.answer(account, &question, none, vec![]));
        }
        let mut blocks = Vec::new();
        let mut shown: Vec<&AskRow> = Vec::new();
        let mut total = 0;
        for r in &rows {
            if blocks.len() >= READ_MAX || total >= CAP_TOTAL {
                break;
            }
            let text = [&r.body_text, &r.ai_summary, &r.snippet].into_iter().flatten().find(|t| !t.is_empty());
            let text = strip(text.map_or("", String::as_str));
            if text.is_empty() {
                continue;
            }
            let mut text = cut(text, CAP_PER_MSG);
            if total + length(text) > CAP_TOTAL {
                text = cut(text, CAP_TOTAL - total);
            }
            total += length(text);
            let who = r.from_name.as_deref().filter(|n| !n.is_empty()).or(r.from_addr.as_deref()).unwrap_or("None");
            let subject = r.subject.as_deref().filter(|s| !s.is_empty()).unwrap_or("(no subject)");
            let date = r.date_iso.as_deref().unwrap_or_default();
            blocks.push(format!("[{}] {who} \u{2014} {subject} ({date})\n{text}", r.pk));
            shown.push(r);
        }
        if blocks.is_empty() {
            let none = "I found related messages but none have readable text indexed yet.";
            return Ok(self.answer(account, &question, none, vec![]));
        }
        let prompt = prompts::ASK.fill(&[("question", &question), ("blocks", &blocks.join("\n\n"))]);
        let got =
            answered(self.calls.call_claude_json(&prompt, &schema(), None, "ask"), "ask failed", "ask unavailable")?;
        let said = got.get("answer").filter(|v| truthy(v)).map(python_str).unwrap_or_default();
        let said = cut(strip(&said), 4000).to_owned();
        if said.is_empty() {
            return Err(unreachable("ask unavailable").into());
        }
        let shown_pks: HashSet<i64> = shown.iter().map(|r| r.pk).collect();
        let sources = cited(got.get("cited_pks"), &shown_pks)
            .into_iter()
            .filter_map(|pk| shown.iter().find(|r| r.pk == pk))
            .map(|r| {
                json!({"key": r.message_key, "folder": r.folder, "uid": r.uid,
                       "from": r.from_name.as_deref().filter(|n| !n.is_empty()).or(r.from_addr.as_deref()),
                       "subject": r.subject.as_deref().filter(|s| !s.is_empty()).unwrap_or("(no subject)"),
                       "snippet": cut(r.snippet.as_deref().unwrap_or_default(), 160),
                       "account": r.account, "date": r.date_iso.as_deref().unwrap_or_default()})
            })
            .collect();
        Ok(self.answer(account, &question, &said, sources))
    }
}
