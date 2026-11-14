//! The Weekly Review (`application.review`): a retrospective beside the daily brief. The last 7 days are counted from
//! the stored mail (no body, no IMAP): what was sent, what came in, who waits on the person and whom they wait on, the
//! reply-later stack and what was cleaned up. One call turns the numbers and the open threads into a short
//! retrospective and up to three picks for the next week. It is kept by the accounts and the person's day, so its cost
//! is paid once a day, and only the prose and the picks are the model's.
//!
//! For someone who turned it on, the week's review is written ahead on Monday morning and read all week: a read then
//! takes the newest kept since Monday, not only today's.

use std::sync::Arc;
use std::time::Duration;

use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Moment};
use mm_kernel::zone::Zone;
use serde_json::{Map, Value, json};

use crate::brief::{number, or};
use crate::briefs::{accounts_key, forget_before, local_day};
use crate::calls::Calls;
use crate::flights::SingleFlight;
use crate::ports::{Around, Cards, Overview, StoredReview};
use crate::prompts;
use crate::refusals::{AiFailure, CallError, answered, unreachable};
use crate::review::{self, AHEAD_DAYS, EVENTS_READ};
use crate::text::{cut, truthy};

pub const WEEK_SECONDS: i64 = 7 * 86_400;
/// The most of each open list.
pub const TOP_OPEN: i64 = 8;
pub const CAP_TOTAL: usize = 9000;
const WAIT: Duration = Duration::from_secs(60);
const MICROS: i64 = 1_000_000;

pub fn schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "narrative": {"type": "string"},
            "next": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["narrative", "next"],
        "additionalProperties": false,
    })
}

/// What a review answers: its prose and picks, the week's numbers and lists, which model wrote it and when, whether it
/// was kept and is stale, and the week it looks back on (`since`, in microseconds).
struct Answer {
    retrospective: (String, Value),
    stats: Map<String, Value>,
    lists: [Vec<Value>; 3],
    made: (Option<String>, String),
    cached: bool,
    stale: bool,
    since: i64,
}

impl Answer {
    fn json(self) -> Value {
        let [needs, waiting, meetings] = self.lists;
        json!({"ok": true, "narrative": self.retrospective.0, "next": self.retrospective.1, "stats": self.stats,
               "needs": needs, "waiting": waiting, "meetings": meetings,
               "week_start": mm_kernel::when::iso(Moment::from_micros(self.since)),
               "model": self.made.0, "generated": self.made.1, "cached": self.cached, "stale": self.stale})
    }
}

/// The week's numbers and its two lists.
struct Week {
    stats: Map<String, Value>,
    needs: Vec<Value>,
    waiting: Vec<Value>,
}

fn parsed(text: &str) -> Result<Value, Fault> {
    serde_json::from_str(text).map_err(|_| Fault::new("JSONDecodeError"))
}

fn listed(value: Value) -> Vec<Value> {
    match value {
        Value::Array(items) => items,
        _ => Vec::new(),
    }
}

#[derive(Debug)]
pub struct Reviews {
    calls: Arc<Calls>,
    cards: Arc<dyn Cards>,
    overview: Arc<dyn Overview>,
    around: Arc<dyn Around>,
    clock: Arc<dyn Clock>,
    zone: Arc<dyn Zone>,
    flights: SingleFlight<(String, String)>,
}

impl Reviews {
    pub fn new(
        calls: Arc<Calls>,
        cards: Arc<dyn Cards>,
        overview: Arc<dyn Overview>,
        around: Arc<dyn Around>,
        clock: Arc<dyn Clock>,
        zone: Arc<dyn Zone>,
    ) -> Reviews {
        Reviews { calls, cards, overview, around, clock, zone, flights: SingleFlight::default() }
    }

    /// Every number and list is local SQL.
    fn aggregate(&self, accounts: &[String], since: i64) -> Result<Week, Fault> {
        let since_iso = mm_kernel::when::iso(Moment::from_micros(since));
        let rows = self.overview.week(accounts, since as f64 / MICROS as f64, &since_iso, TOP_OPEN)?;
        let mut by_bucket = Map::new();
        for (bucket, n) in rows.by_bucket {
            by_bucket.insert(or(&bucket, "other").to_owned(), json!(n));
        }
        let needs: Vec<Value> = rows
            .needs
            .iter()
            .map(|r| {
                let who = [&r.from_name, &r.from_addr].into_iter().flatten().find(|s| !s.is_empty());
                json!({"key": r.message_key, "folder": r.folder, "uid": r.uid, "account": r.account,
                       "thread_id": r.thread_id, "who": who.map_or("someone", String::as_str),
                       "subject": or(&r.subject, "(no subject)"), "importance": r.importance,
                       "action": r.action.as_deref().unwrap_or_default()})
            })
            .collect();
        let waiting: Vec<Value> = rows
            .waiting
            .iter()
            .map(|r| {
                let to = r
                    .recipients
                    .as_deref()
                    .filter(|t| !t.is_empty())
                    .map_or("", |t| mm_kernel::text::strip(t.split(',').next().unwrap_or_default()));
                json!({"account": r.account, "thread_id": r.thread_id, "key": r.message_key, "folder": r.folder,
                       "uid": r.uid, "subject": or(&r.subject, "(no subject)"), "to": to,
                       "sent_ts": r.first_sent_ts})
            })
            .collect();
        let mut stats = Map::new();
        stats.insert("sent".into(), json!(rows.sent));
        stats.insert("received".into(), json!(rows.received));
        stats.insert("by_bucket".into(), Value::Object(by_bucket));
        stats.insert("needs_you".into(), json!(needs.len()));
        stats.insert("waiting_on".into(), json!(waiting.len()));
        stats.insert("reply_later".into(), json!(rows.reply_later));
        stats.insert("cleaned".into(), json!(rows.cleaned.unwrap_or(0)));
        stats.insert("handled".into(), json!(rows.handled));
        Ok(Week { stats, needs, waiting })
    }

    /// The week's meetings and deadlines, from the invites in the mail. A calendar that can't be read costs the review
    /// only its mention of them.
    fn meetings(&self, since: i64) -> Vec<Value> {
        let now = self.clock.now().micros().div_euclid(MICROS);
        let since = since.div_euclid(MICROS);
        let (gone, coming) =
            match (self.around.events_from(since, EVENTS_READ), self.around.events_from(now, EVENTS_READ)) {
                (Ok(gone), Ok(coming)) => (gone, coming),
                (Err(fault), _) | (_, Err(fault)) => {
                    mm_kernel::log::warning("ai", "the week's meetings can't be read", &[], Some(fault.class));
                    return Vec::new();
                }
            };
        let local = |at: i64| mm_kernel::zone::local(&*self.zone, at);
        let until = review::minutes_iso(local(now) + AHEAD_DAYS * 86_400);
        review::meetings_of(
            &gone,
            &coming,
            &review::minutes_iso(local(since)),
            &review::minutes_iso(local(now)),
            &until,
        )
    }

    /// A kept list as the page reads it: each with the folder and uid of its message's copy now. One whose message has
    /// gone is left out, one that named no message stays as it was, and a pk a list once held goes.
    fn items_now(&self, items: Vec<Value>) -> Result<Vec<Value>, Fault> {
        let items: Vec<Map<String, Value>> = items
            .into_iter()
            .filter_map(|item| match item {
                Value::Object(mut item) => {
                    item.shift_remove("pk");
                    Some(item)
                }
                _ => None,
            })
            .collect();
        let named = |item: &Map<String, Value>| item.get("key").filter(|k| truthy(k)).and_then(Value::as_i64);
        let asked: Vec<(i64, Option<String>)> = items
            .iter()
            .filter_map(|item| Some((named(item)?, item.get("folder").and_then(Value::as_str).map(str::to_owned))))
            .collect();
        let mut now = self.overview.copies_now(&asked)?.into_iter();
        let mut out = Vec::new();
        for mut item in items {
            if !item.get("key").is_some_and(truthy) {
                let mut first = Map::new();
                first.insert("uid".into(), Value::Null);
                first.extend(item);
                out.push(Value::Object(first));
            } else if named(&item).is_some()
                && let Some(Some((folder, uid))) = now.next()
            {
                item.insert("folder".into(), json!(folder));
                item.insert("uid".into(), json!(uid));
                out.push(Value::Object(item));
            }
        }
        Ok(out)
    }

    fn stored(
        &self,
        written_on: &str,
        row: &StoredReview,
        today: &str,
        week: &Week,
        fp: &str,
        since: i64,
    ) -> Result<Value, AiFailure> {
        let mut said = match parsed(&row.stats)? {
            Value::Object(said) => said,
            _ => Map::new(),
        };
        let since = if written_on == today {
            // An Undo changes it: as it stands, not as it was written.
            said.insert("handled".into(), week.stats["handled"].clone());
            since
        } else {
            // An earlier day's, read all week: the week it looked back on, as it said it.
            if !said.contains_key("handled") {
                said.insert("handled".into(), week.stats["handled"].clone());
            }
            let written = mm_kernel::isoformat::read(&row.created_at).ok_or_else(|| Fault::new("ValueError"))?;
            let at = match written.offset {
                Some(offset) => written.micros_since_epoch() - offset,
                None => {
                    let placed = mm_kernel::zone::place(&*self.zone, written.micros_since_epoch().div_euclid(MICROS));
                    placed * MICROS + i64::from(written.microsecond)
                }
            };
            at - WEEK_SECONDS * MICROS
        };
        let needs = self.items_now(listed(parsed(&row.needs)?))?;
        let waiting = self.items_now(listed(parsed(&row.waiting)?))?;
        let meetings = self.items_now(listed(said.shift_remove("meetings").unwrap_or(Value::Null)))?;
        let next = parsed(&row.next)?;
        let answer = Answer {
            retrospective: (row.narrative.clone(), next),
            stats: said,
            lists: [needs, waiting, meetings],
            made: (row.model.clone(), row.created_at.clone()),
            cached: true,
            stale: row.fingerprint != fp,
            since,
        };
        Ok(answer.json())
    }

    /// The newest review kept on one of `days`, newest first, and its day.
    fn stored_row(&self, key: &str, days: &[String]) -> Result<Option<(String, StoredReview)>, Fault> {
        for day in days {
            if let Some(row) = self.cards.review(key, day)? {
                return Ok(Some((day.clone(), row)));
            }
        }
        Ok(None)
    }

    /// The week's review for `accounts`: what is kept for today, or with `week` since Monday, unless `force`. With
    /// `generate` false it only reads, and says what is missing; else the one missing is written, by one request at a
    /// time.
    pub fn build(
        &self,
        accounts: &[String],
        force: bool,
        generate: bool,
        feature: &str,
        week: bool,
    ) -> Result<Value, AiFailure> {
        let now = self.clock.now();
        let since = now.micros() - WEEK_SECONDS * MICROS;
        let today = mm_kernel::zone::date(&*self.zone, now.whole_seconds());
        let (key, day) = (accounts_key(accounts), local_day(&*self.zone, now));
        let days = if week { review::week_so_far(today) } else { vec![day.clone()] };
        let counted = self.aggregate(accounts, since)?;
        let cached = self.stored_row(&key, &days)?;
        let fp = review::fingerprint_of(&counted.stats, &counted.needs, &counted.waiting);
        if let Some((written_on, row)) = cached.filter(|_| !force) {
            return self.stored(&written_on, &row, &day, &counted, &fp, since);
        }
        if !generate {
            return Ok(json!({"ok": true, "missing": true}));
        }
        let read = || match self.stored_row(&key, &days) {
            Ok(again) => again.map(|(written_on, row)| self.stored(&written_on, &row, &day, &counted, &fp, since)),
            Err(fault) => Some(Err(fault.into())),
        };
        let make = || self.make(&counted, &key, &day, &fp, feature, since);
        let answer = self.flights.run((key.clone(), day.clone()), make, read, WAIT, !force);
        answer.unwrap_or_else(|| Err(unreachable("review unavailable").into()))
    }

    fn make(&self, week: &Week, key: &str, day: &str, fp: &str, feature: &str, since: i64) -> Result<Value, AiFailure> {
        let Week { stats, needs, waiting } = week;
        let mut buckets: Vec<(&String, i64)> =
            stats["by_bucket"].as_object().into_iter().flatten().map(|(k, v)| (k, v.as_i64().unwrap_or(0))).collect();
        buckets.sort_by_key(|(_, n)| std::cmp::Reverse(*n));
        let bl: Vec<String> = buckets.iter().take(5).map(|(k, v)| format!("{k}: {v}")).collect();
        let n = |name: &str| stats[name].as_i64().unwrap_or(0);
        let stats_txt = format!(
            "Sent: {}\nReceived in inbox: {} ({})\nStill need your reply: {}\nWaiting to hear back: {}\n\
             Pinned to reply-later: {}\nSenders cleaned up: {}\nFiled away: {}",
            n("sent"),
            n("received"),
            bl.join(", "),
            n("needs_you"),
            n("waiting_on"),
            n("reply_later"),
            n("cleaned"),
            n("handled")
        );
        let meetings = self.meetings(since);
        let text = |item: &Value, name: &str| item.get(name).and_then(Value::as_str).unwrap_or_default().to_owned();
        let needs_lines: Vec<String> = needs
            .iter()
            .map(|x| {
                let action = text(x, "action");
                let action = if action.is_empty() { String::new() } else { format!(" \u{2014} {}", cut(&action, 120)) };
                let importance = number(x.get("importance").and_then(Value::as_i64));
                format!("- [{importance}] {} \u{2014} {}{action}", text(x, "who"), text(x, "subject"))
            })
            .collect();
        let waiting_lines: Vec<String> =
            waiting.iter().map(|w| format!("- {} \u{2192} {}", text(w, "subject"), text(w, "to"))).collect();
        let or_nothing = |lines: Vec<String>| if lines.is_empty() { "(nothing)".to_owned() } else { lines.join("\n") };
        let (needs_txt, waiting_txt) = (or_nothing(needs_lines), or_nothing(waiting_lines));
        let prompt = prompts::REVIEW.fill(&[
            ("stats", &stats_txt),
            ("meetings", &review::meeting_lines(&meetings)),
            ("needs", cut(&needs_txt, CAP_TOTAL)),
            ("waiting", cut(&waiting_txt, 2000)),
        ]);
        let made: Result<_, CallError> = self.calls.call_claude_json(&prompt, &schema(), None, feature);
        let answer = answered(made, "review failed", "review unavailable")?;
        let (mut narrative, mut next) = review::clean(&answer);
        let mut model = self.calls.model_label();
        if review::degenerate(&narrative) {
            // The numbers still make a useful retrospective.
            let (fallback, picks) = review::fallback(stats, needs);
            narrative = fallback;
            if next.is_empty() {
                next = picks;
            }
            model = "fallback".into();
        }
        let created = mm_kernel::when::iso(self.clock.now());
        let mut kept_stats = stats.clone();
        kept_stats.insert("meetings".into(), Value::Array(meetings.clone()));
        let row = StoredReview {
            fingerprint: fp.to_owned(),
            narrative: narrative.clone(),
            next: json!(next).to_string(),
            stats: Value::Object(kept_stats).to_string(),
            needs: Value::Array(needs.clone()).to_string(),
            waiting: Value::Array(waiting.clone()).to_string(),
            model: Some(model.clone()),
            created_at: created.clone(),
        };
        self.cards.save_review(key, day, &row, &forget_before(self.clock.now()))?;
        let answer = Answer {
            retrospective: (narrative, json!(next)),
            stats: stats.clone(),
            lists: [needs.clone(), waiting.clone(), meetings],
            made: (Some(model), created),
            cached: false,
            stale: false,
            since,
        };
        Ok(answer.json())
    }
}
