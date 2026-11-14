//! The "Today" card and "since you last looked" (`application.brief`). A brief is made from the mail the AI already
//! summarized (no body, no IMAP), in one call under a schema, and kept by the accounts and the person's day, so its cost
//! is paid once a day. A delta is the same over only the mail newer than a tab's watermark, kept by the watermark and
//! the messages it was made from. The rows' text goes to the model as untrusted data, and the ids it names are only
//! those it was shown.

use std::collections::HashSet;
use std::sync::Arc;
use std::time::Duration;

use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Moment};
use mm_kernel::zone::Zone;
use serde_json::{Value, json};

use crate::brief::{self, DELTA_TOP_N, Group, number, or, who};
use crate::calls::Calls;
use crate::digest::fingerprint;
use crate::flights::SingleFlight;
use crate::ports::{Cards, Glance, Overview, StoredCard};
use crate::prompts;
use crate::refusals::{AiFailure, answered, unreachable};
use crate::text::{cut, length};

pub const TOP_N: i64 = 40;
pub const CAP_TOTAL: usize = 12000;
pub const DELTA_CAP: usize = 4000;
/// How long a second request waits on the first's brief, and on its delta.
const BRIEF_WAIT: Duration = Duration::from_secs(60);
const DELTA_WAIT: Duration = Duration::from_secs(30);
/// How long a brief, a delta or a review is kept: a week keeps each table tiny.
pub const KEPT_SECONDS: i64 = 7 * 86_400;

pub fn briefing_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "narrative": {"type": "string"},
            "groups": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "count": {"type": "integer"},
                    "pks": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["label", "count", "pks"],
            }},
        },
        "required": ["narrative", "groups"],
    })
}

pub fn delta_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "headline": {"type": "string"},
            "items": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "who": {"type": "string"},
                    "gist": {"type": "string"},
                    "importance": {"type": "integer"},
                },
                "required": ["who", "gist"],
            }},
        },
        "required": ["headline"],
    })
}

/// The accounts as a stored card names them: sorted, once each, comma-joined.
pub fn accounts_key(accounts: &[String]) -> String {
    let mut sorted: Vec<&str> = accounts.iter().map(String::as_str).collect();
    sorted.sort_unstable();
    sorted.dedup();
    sorted.join(",")
}

/// The person's day, as `YYYY-MM-DD`: it rolls at their own midnight.
pub fn local_day(zone: &dyn Zone, now: Moment) -> String {
    let (y, m, d) = mm_kernel::zone::date(zone, now.whole_seconds());
    format!("{y:04}-{m:02}-{d:02}")
}

/// When what is kept is forgotten: a week before now.
pub fn forget_before(now: Moment) -> String {
    mm_kernel::when::iso(Moment::from_micros(now.micros() - KEPT_SECONDS * 1_000_000))
}

fn parsed(text: &str) -> Result<Value, Fault> {
    serde_json::from_str(text).map_err(|_| Fault::new("JSONDecodeError"))
}

/// What a brief or a delta was made from, by Message-ID.
fn made_from(rows: &[Glance]) -> String {
    fingerprint(rows.iter().map(|r| r.message_id.as_deref()), 0)
}

/// A line the model reads of a row: `cap` characters of its summary.
fn line(r: &Glance, category: &str, cap: usize) -> String {
    format!(
        "[{}] (imp {}, {category}) {} \u{2014} {} \u{2014} {}",
        r.pk,
        number(r.importance),
        who(r),
        or(&r.subject, "(no subject)"),
        cut(r.summary.as_deref().unwrap_or_default(), cap)
    )
}

/// The lines that fit in `cap` characters, stopping at the first that doesn't.
fn lines_within(lines: impl Iterator<Item = String>, cap: usize) -> Vec<String> {
    let mut out = Vec::new();
    let mut total = 0;
    for line in lines {
        if total + length(&line) > cap {
            break;
        }
        total += length(&line);
        out.push(line);
    }
    out
}

#[derive(Debug)]
pub struct Briefs {
    calls: Arc<Calls>,
    cards: Arc<dyn Cards>,
    overview: Arc<dyn Overview>,
    clock: Arc<dyn Clock>,
    zone: Arc<dyn Zone>,
    briefs: SingleFlight<(String, String)>,
    deltas: SingleFlight<(String, i64, String)>,
}

impl Briefs {
    pub fn new(
        calls: Arc<Calls>,
        cards: Arc<dyn Cards>,
        overview: Arc<dyn Overview>,
        clock: Arc<dyn Clock>,
        zone: Arc<dyn Zone>,
    ) -> Briefs {
        Briefs { calls, cards, overview, clock, zone, briefs: SingleFlight::default(), deltas: SingleFlight::default() }
    }

    fn now(&self) -> String {
        mm_kernel::when::iso(self.clock.now())
    }

    /// A stored brief's groups as the page reads them: each message by its key and the folder its copy is in now. One
    /// whose message has gone is left out.
    fn groups_now(&self, groups: &str) -> Result<Vec<Value>, Fault> {
        let groups = parsed(groups)?;
        let groups = groups.as_array().map(Vec::as_slice).unwrap_or_default();
        let mut out = Vec::new();
        for g in groups {
            let messages: Vec<(i64, Option<String>)> = g
                .get("messages")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(|m| {
                    let key = m.get(0).and_then(Value::as_i64)?;
                    Some((key, m.get(1).and_then(Value::as_str).map(str::to_owned)))
                })
                .collect();
            let now = self.overview.copies_now(&messages)?;
            let there: Vec<Value> = messages
                .iter()
                .zip(now)
                .filter_map(|((key, _), now)| {
                    now.map(|(folder, uid)| json!({"key": key, "folder": folder, "uid": uid}))
                })
                .collect();
            out.push(json!({"label": g.get("label").cloned().unwrap_or(Value::Null), "count": there.len(),
                            "messages": there}));
        }
        Ok(out)
    }

    fn stored(&self, row: &StoredCard, fp: &str) -> Result<Value, AiFailure> {
        let groups = self.groups_now(&row.body)?;
        Ok(json!({"ok": true, "narrative": row.text, "groups": groups, "model": row.model,
                  "generated": row.created_at, "cached": true, "stale": row.fingerprint != fp}))
    }

    /// The "Today" card for `accounts`: what is stored for today, unless `force`. With `generate` false it only reads,
    /// and says what is missing; else the one missing is written, by one request at a time.
    pub fn build(&self, accounts: &[String], force: bool, generate: bool, feature: &str) -> Result<Value, AiFailure> {
        let (key, day) = (accounts_key(accounts), local_day(&*self.zone, self.clock.now()));
        let rows = self.overview.brief_rows(accounts, TOP_N)?;
        let cached = self.cards.brief(&key, &day)?;
        let fp = made_from(&rows);
        if let Some(cached) = cached.filter(|_| !force) {
            return self.stored(&cached, &fp);
        }
        if rows.is_empty() {
            return Ok(json!({"ok": true, "narrative": "Nothing needs you right now \u{2014} your inbox is quiet.",
                             "groups": [], "model": "empty", "generated": self.now(), "cached": false,
                             "stale": false}));
        }
        if !generate {
            return Ok(json!({"ok": true, "missing": true, "narrative": "", "groups": [], "cached": false,
                             "stale": false}));
        }
        let read = || match self.cards.brief(&key, &day) {
            Ok(row) => row.map(|row| self.stored(&row, &fp)),
            Err(fault) => Some(Err(fault.into())),
        };
        let make = || self.make_brief(&rows, &key, &day, &fp, feature);
        let answer = self.briefs.run((key.clone(), day.clone()), make, read, BRIEF_WAIT, !force);
        answer.unwrap_or_else(|| Err(unreachable("briefing unavailable").into()))
    }

    fn make_brief(&self, rows: &[Glance], key: &str, day: &str, fp: &str, feature: &str) -> Result<Value, AiFailure> {
        let valid: HashSet<i64> = rows.iter().map(|r| r.pk).collect();
        let lines = lines_within(rows.iter().map(|r| line(r, or(&r.category, "other"), 160)), CAP_TOTAL);
        let prompt = prompts::BRIEF.fill(&[("items", &lines.join("\n"))]);
        let made = self.calls.call_claude_json(&prompt, &briefing_schema(), None, feature);
        let answer = answered(made, "briefing failed", "briefing unavailable")?;
        let (mut narrative, mut groups) = brief::clean(&answer, &valid);
        let mut model = self.calls.model_label();
        if narrative.is_empty() {
            // Replied, but nothing usable: a card made from the rows is kept instead.
            (narrative, groups) = brief::fallback(rows);
            model = "fallback".into();
        }
        let created = self.now();
        let body = stored_groups(&groups, rows).to_string();
        let card =
            StoredCard { fingerprint: fp.to_owned(), text: narrative, body, model: Some(model), created_at: created };
        self.cards.save_brief(key, day, &card, &forget_before(self.clock.now()))?;
        Ok(json!({"ok": true, "narrative": card.text, "groups": self.groups_now(&card.body)?, "model": card.model,
                  "generated": card.created_at, "cached": false, "stale": false}))
    }

    /// "Since you last looked": the mail newer than `since_ts` in `accounts`, as a headline and a few items. With none,
    /// it is empty; with `generate` false it only reads.
    pub fn build_delta(&self, accounts: &[String], since_ts: i64, generate: bool) -> Result<Value, AiFailure> {
        let rows = self.overview.delta_rows(accounts, since_ts, DELTA_TOP_N)?;
        if rows.is_empty() {
            return Ok(json!({"ok": true, "empty": true, "count": 0}));
        }
        // Kept by what it was made from: more mail since the same watermark writes it again, and an unchanged window
        // stays free to read.
        let (key, fp) = (accounts_key(accounts), made_from(&rows));
        let read = || match self.cards.delta(&key, since_ts, &fp) {
            Ok(row) => row.map(|row| -> Result<Value, AiFailure> {
                Ok(json!({"ok": true, "empty": false, "headline": row.text, "items": parsed(&row.body)?,
                          "count": rows.len(), "cached": true}))
            }),
            Err(fault) => Some(Err(fault.into())),
        };
        if !generate {
            return read()
                .unwrap_or_else(|| Ok(json!({"ok": true, "empty": false, "missing": true, "count": rows.len()})));
        }
        if let Some(stored) = read() {
            return stored;
        }
        let make = || self.make_delta(&rows, &key, since_ts, &fp);
        let answer = self.deltas.run((key.clone(), since_ts, fp.clone()), make, read, DELTA_WAIT, true);
        answer.unwrap_or_else(|| Err(unreachable("delta unavailable").into()))
    }

    fn make_delta(&self, rows: &[Glance], key: &str, since_ts: i64, fp: &str) -> Result<Value, AiFailure> {
        let lines = lines_within(rows.iter().map(|r| line(r, or(&r.category, ""), 120)), DELTA_CAP);
        let prompt = prompts::DELTA.fill(&[("count", &lines.len().to_string()), ("items", &lines.join("\n"))]);
        let made = self.calls.call_claude_json(&prompt, &delta_schema(), None, "delta");
        let answer = answered(made, "delta failed", "delta unavailable")?;
        let (mut headline, mut items) = brief::clean_delta(&answer);
        let mut model = self.calls.model_label();
        if headline.is_empty() {
            (headline, items) = brief::delta_fallback(rows);
            model = "fallback".into();
        }
        let card = StoredCard {
            fingerprint: fp.to_owned(),
            text: headline,
            body: Value::Array(items).to_string(),
            model: Some(model),
            created_at: self.now(),
        };
        self.cards.save_delta(key, since_ts, &card, &forget_before(self.clock.now()))?;
        Ok(json!({"ok": true, "empty": false, "headline": card.text, "items": parsed(&card.body)?,
                  "count": rows.len(), "cached": false}))
    }
}

/// The groups as kept: each message by its key and the folder it was read from, since a pk doesn't last.
fn stored_groups(groups: &[Group], rows: &[Glance]) -> Value {
    let at = |pk: i64| rows.iter().find(|r| r.pk == pk).map(|r| json!([r.message_key, r.folder]));
    Value::Array(
        groups
            .iter()
            .map(|g| json!({"label": g.label, "messages": g.pks.iter().filter_map(|p| at(*p)).collect::<Vec<_>>()}))
            .collect(),
    )
}
