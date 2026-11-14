//! What the Assistant answers a person who asks (`tests/characterization/assistant/test_thread_summary.py`,
//! `test_reply_drafting.py` and the domain's tests): what is sent, what is kept, and what comes back. The expected
//! texts are what the Python app gives for the same input.

use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::{Arc, Mutex};

use mm_assistant::action_plan::{out_of_reach, preset_told, verb_told};
use mm_assistant::ask::{Asker, cited, fts_match};
use mm_assistant::bulk::BulkCommands;
use mm_assistant::calls::Calls;
use mm_assistant::context::{Context, trim};
use mm_assistant::digest::{fingerprint, parse};
use mm_assistant::gateway::{Budget, Gateway};
use mm_assistant::governor::CostGovernor;
use mm_assistant::ports::{
    AccountList, AiSettings, Answers, AskIndex, AskRow, ConversationBy, Engine, InForce, LanguageModel, Mail, Message,
    ModelError, NewDraft, PlanSearch, Prompt, Reached, Reply, Said, Spend, Stamp, StoredDraft, StoredSummary, Usage,
    UsageLedger,
};
use mm_assistant::prepared_replies::{PreparedReplies, ReplyAsked};
use mm_assistant::refusals::{AiFailure, NotSent};
use mm_assistant::reply_draft::ReplyDrafts;
use mm_assistant::status::Status;
use mm_assistant::thread_digest::{Asked, ThreadDigest};
use mm_kernel::errors::{Failure, Fault, Kind};
use mm_kernel::seams::{Clock, Moment};
use mm_kernel::zone::Utc;
use serde_json::{Value, json};

#[derive(Debug)]
struct Frozen(AtomicI64);

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(self.0.load(Ordering::SeqCst))
    }
}

/// Monday 2 November 2026, noon UTC.
const NOW: i64 = 1_793_620_800;
const ADA: &str = "ada@example.test";

/// Answers each prompt with the next of its replies, or a stub object, and keeps the prompts.
#[derive(Debug, Default)]
struct Model {
    replies: Mutex<Vec<Result<String, ModelError>>>,
    prompts: Mutex<Vec<Prompt>>,
}

impl LanguageModel for Model {
    fn complete(&self, prompt: &Prompt) -> Result<Reply, ModelError> {
        self.prompts.lock().expect("prompts").push(prompt.clone());
        let mut replies = self.replies.lock().expect("replies");
        let text = if replies.is_empty() {
            Ok("{\"body\": \"stub body\", \"text\": \"stub text\"}".into())
        } else {
            replies.remove(0)
        }?;
        Ok(Reply { text, model: prompt.model.clone(), usage: Usage::default(), stop_reason: "end_turn".into() })
    }
}

#[derive(Debug)]
struct Keyed(Option<Arc<dyn LanguageModel>>);

impl Engine for Keyed {
    fn in_force(&self) -> InForce {
        match &self.0 {
            Some(model) => InForce::On { model: model.clone(), key_from: "ANTHROPIC_API_KEY" },
            None => InForce::Off { key_from: "ANTHROPIC_API_KEY" },
        }
    }
}

#[derive(Debug, Default)]
struct Ledger;

impl UsageLedger for Ledger {
    fn record(&self, _spend: &Spend) -> Result<(), Fault> {
        Ok(())
    }
    fn spent(&self, _day: &str) -> Result<i64, Fault> {
        Ok(0)
    }
    fn reserved(&self) -> Result<i64, Fault> {
        Ok(0)
    }
    fn in_batches(&self) -> Result<i64, Fault> {
        Ok(0)
    }
}

#[derive(Debug)]
struct NoLimit;

impl AiSettings for NoLimit {
    fn key_named(&self) -> Result<bool, Fault> {
        Ok(true)
    }
    fn daily_limit(&self) -> Result<Option<Value>, Fault> {
        Ok(None)
    }
}

/// Lin and Ada's lunch thread, and a lone message with no thread.
#[derive(Debug, Default)]
struct Mailbox {
    thread: Vec<Said>,
}

fn said(pk: i64, from: (&str, &str), body: &str, ts: i64) -> Said {
    Said {
        pk,
        account: ADA.into(),
        from_name: Some(from.0.into()),
        from_addr: Some(from.1.into()),
        date_iso: Some(format!("2026-10-27T0{pk}:00:00+00:00")),
        subject: Some("Lunch on Thursday?".into()),
        snippet: Some(body.chars().take(20).collect()),
        body_text: Some(body.into()),
        message_id: Some(format!("<lunch-{pk}@example.test>")),
        body_fetched: true,
        ts: Some(ts),
    }
}

fn lunch() -> Mailbox {
    let lin = ("Lin Mori", "lin@example.test");
    Mailbox {
        thread: vec![
            said(1, lin, "Are you free for lunch on Thursday?\n> an old quote", 100),
            said(2, ("Ada Park", ADA), "Thursday works.\nOn Mon, Lin wrote:\n> Are you free", 200),
            said(3, lin, "12:30 it is.", 300),
        ],
    }
}

impl Mail for Mailbox {
    fn message(&self, pk: i64) -> Result<Option<Message>, Fault> {
        Ok(self.thread.iter().find(|s| s.pk == pk).map(|s| Message {
            pk,
            account: ADA.into(),
            thread_id: Some("t-lunch".into()),
            message_id: s.message_id.clone(),
            message_key: Some(pk * 10),
            from_addr: s.from_addr.clone(),
            folder: Some("INBOX".into()),
            ..Message::default()
        }))
    }
    fn conversation(&self, account: &str, by: ConversationBy, value: &str) -> Result<Vec<Said>, Fault> {
        Ok(if account == ADA && by == ConversationBy::Thread && value == "t-lunch" {
            self.thread.clone()
        } else {
            vec![]
        })
    }
    fn to_reply_to(&self, pk: i64) -> Result<Vec<Said>, Fault> {
        Ok(self.thread.iter().filter(|s| s.pk == pk).cloned().collect())
    }
    fn fetch_text(&self, _pk: i64) -> Result<Option<String>, Fault> {
        Ok(None)
    }
    fn thread_stamps(&self, _account: &str, _thread_id: &str) -> Result<Vec<Stamp>, Fault> {
        Ok(self.thread.iter().map(|s| (s.message_id.clone(), s.ts)).collect())
    }
    fn message_stamp(&self, _pk: i64) -> Result<Option<Stamp>, Fault> {
        Ok(None)
    }
    fn thread_anchor(&self, _account: &str, _thread_id: &str) -> Result<Option<i64>, Fault> {
        Ok(Some(3))
    }
}

#[derive(Debug)]
struct Ada;

impl AccountList for Ada {
    fn emails(&self) -> Result<Vec<String>, Fault> {
        Ok(vec![ADA.into()])
    }
}

/// What the Assistant keeps, in memory.
#[derive(Debug, Default)]
struct Kept {
    summaries: Mutex<HashMap<(String, String, String), StoredSummary>>,
    drafts: Mutex<HashMap<(String, String), StoredDraft>>,
}

impl Answers for Kept {
    fn thread_summary(&self, account: &str, thread_id: &str, scope: &str) -> Result<Option<StoredSummary>, Fault> {
        let key = (account.to_owned(), thread_id.to_owned(), scope.to_owned());
        Ok(self.summaries.lock().expect("summaries").get(&key).cloned())
    }
    fn save_thread_summary(&self, s: &StoredSummary) -> Result<(), Fault> {
        let key = (s.account.clone(), s.thread_id.clone(), s.scope.clone());
        self.summaries.lock().expect("summaries").insert(key, s.clone());
        Ok(())
    }
    fn draft_ahead(&self, account: &str, ref_key: &str) -> Result<Option<StoredDraft>, Fault> {
        Ok(self.drafts.lock().expect("drafts").get(&(account.to_owned(), ref_key.to_owned())).cloned())
    }
    fn put_draft_ahead(&self, account: &str, ref_key: &str, d: &NewDraft<'_>, now: i64) -> Result<(), Fault> {
        let stored = StoredDraft {
            fp: Some(d.fp.into()),
            body: Some(d.body.into()),
            tone: d.tone.map(str::to_owned),
            model: d.model.map(str::to_owned),
            created_ts: now,
            ..StoredDraft::default()
        };
        self.drafts.lock().expect("drafts").insert((account.to_owned(), ref_key.to_owned()), stored);
        Ok(())
    }
    fn draft_ahead_failed(&self, _account: &str, _ref_key: &str, _now: i64) -> Result<(), Fault> {
        Ok(())
    }
    fn draft_ahead_touched(&self, _account: &str, _ref_key: &str, _now: i64) -> Result<(), Fault> {
        Ok(())
    }
    fn draft_ahead_gc(&self, _now: i64, _orphans: bool) -> Result<(), Fault> {
        Ok(())
    }
    fn reply_worthy(&self, _pk: i64) -> Result<bool, Fault> {
        Ok(true)
    }
}

struct World {
    model: Arc<Model>,
    kept: Arc<Kept>,
    calls: Arc<Calls>,
    digest: ThreadDigest,
    drafting: PreparedReplies,
}

fn world(on: bool) -> World {
    let model = Arc::new(Model::default());
    let clock: Arc<dyn Clock> = Arc::new(Frozen(AtomicI64::new(NOW * 1_000_000)));
    let budget = Arc::new(Budget {
        ledger: Arc::new(Ledger),
        settings: Arc::new(NoLimit),
        clock: clock.clone(),
        zone: Arc::new(Utc),
    });
    let governor = Arc::new(CostGovernor::new(budget.clone(), clock.clone()));
    let lm: Arc<dyn LanguageModel> = model.clone();
    let gateway = Arc::new(Gateway::new(Arc::new(Keyed(on.then_some(lm))), budget, governor));
    let status = Arc::new(Status::new(gateway.clone(), clock.clone(), "claude-opus-5-5".into()));
    let calls = Arc::new(Calls::new(gateway, status, None, false));
    let kept = Arc::new(Kept::default());
    let context = Context::new(Arc::new(lunch()), Arc::new(Ada));
    let digest = ThreadDigest::new(calls.clone(), kept.clone(), context.clone(), clock.clone());
    let replies = Arc::new(ReplyDrafts::new(calls.clone(), context.clone()));
    let drafting =
        PreparedReplies::new(kept.clone(), context, replies, Box::new(|local: &str| local != "noreply"), clock);
    World { model, kept, calls, digest, drafting }
}

fn sent(w: &World) -> Vec<Prompt> {
    std::mem::take(&mut *w.model.prompts.lock().expect("prompts"))
}

#[test]
fn a_template_is_filled_in_one_pass_and_braces_of_its_own_stay() {
    let filled = mm_assistant::prompts::fill("{a} and {b} {not this} {\"json\": 1}", &[("a", "{b}"), ("b", "B")]);
    assert_eq!(filled, "{b} and B {not this} {\"json\": 1}");
}

#[test]
fn what_the_model_reads_of_a_message_stops_at_its_quoted_history() {
    assert_eq!(trim("Thanks!\n> quoted\nOn Mon, Ada wrote:\n> more"), "Thanks!");
    assert_eq!(trim("---- Forwarded message\nbelow"), "");
    assert_eq!(trim("From the desk of Lin\nFrom: Lin <lin@example.test>\nold"), "From the desk of Lin");
}

#[test]
fn a_fingerprint_and_a_loose_summary_read_as_the_python_app_reads_them() {
    let ids = [Some("<b@example.test>"), None, Some("<a@example.test>")];
    assert_eq!(fingerprint(ids, NOW), "t2:3:1793620800:513ecf42");
    let parsed =
        parse("Sure: {\"tldr\": 5, \"points\": [1, \"two\", [3]], \"participants\": [true]} ok").expect("JSON");
    assert_eq!(
        (parsed.tldr.as_str(), parsed.points, parsed.participants),
        ("5", vec!["1".into(), "two".into(), "[3]".into()], vec!["True".to_owned()])
    );
    assert_eq!(parse("no JSON"), None);
}

#[test]
fn a_thread_is_summarized_once_then_read_from_what_is_kept() {
    let w = world(true);
    w.model
        .replies
        .lock()
        .expect("replies")
        .push(Ok("{\"tldr\": \"Lunch is Thursday.\", \"points\": [\"set\"]}".into()));
    let asked = Asked { pk: Some(3), ..Asked::default() };
    let first = w.digest.asked(&asked).expect("a summary").expect("made");
    assert_eq!(
        (first["cached"].clone(), first["tldr"].clone(), first["participants"].clone(), first["message_count"].clone()),
        (json!(false), json!("Lunch is Thursday."), json!(["Ada Park", "Lin Mori"]), json!(3))
    );
    assert_eq!(first["created_at"], "2026-11-02T12:00:00+00:00");
    let [prompt] = sent(&w).try_into().expect("one call");
    assert!(prompt.schema.is_none());
    assert!(prompt.text.contains(
        "Messages, oldest first (3):\n--- [1] Lin Mori <lin@example.test> \u{2014} 2026-10-27T01:00:00+00:00\nAre you free for lunch on Thursday?\n--- [2] Ada Park <ada@example.test> \u{2014} 2026-10-27T02:00:00+00:00\nThursday works.\n"
    ));
    let again = w.digest.asked(&asked).expect("a summary").expect("kept");
    assert_eq!(
        (again["cached"].clone(), again["stale"].clone(), again["points"].clone()),
        (json!(true), json!(false), json!(["set"]))
    );
    assert!(sent(&w).is_empty());
    let stored = w.kept.summaries.lock().expect("kept").values().next().cloned().expect("one kept");
    assert_eq!(stored.points.as_deref(), Some("[\"set\"]"));
}

#[test]
fn a_reply_that_isnt_json_keeps_a_fallback_and_an_account_not_used_is_refused() {
    let w = world(true);
    w.model.replies.lock().expect("replies").push(Ok("Just prose.".into()));
    let made =
        w.digest.asked(&Asked { pk: Some(1), force: true, ..Asked::default() }).expect("a summary").expect("made");
    assert_eq!(
        (made["model"].clone(), made["tldr"].clone(), made["points"].clone()),
        (json!("parse-error"), json!("Lunch on Thursday?"), json!(["12:30 it is."]))
    );
    let refused = w.digest.asked(&Asked {
        account: Some("zed@example.test".into()),
        thread_id: Some("t".into()),
        ..Asked::default()
    });
    assert!(matches!(refused, Err(AiFailure::Failure(Failure::Problem(p))) if p.kind == Kind::Refused));
}

#[test]
fn with_ai_off_nothing_is_kept_and_the_route_is_told() {
    let w = world(false);
    let off = w.digest.asked(&Asked { pk: Some(3), ..Asked::default() });
    assert_eq!(off, Err(AiFailure::NotSent(NotSent::AiOff)));
    assert!(w.kept.summaries.lock().expect("kept").is_empty());
    let rewrite = mm_assistant::rewrite::rewrite(&w.calls, "teh", "fix", "English");
    assert_eq!(rewrite, Err(AiFailure::NotSent(NotSent::AiOff)));
}

#[test]
fn a_rewrite_says_its_op_and_refuses_what_it_cant_do() {
    let w = world(true);
    let done = mm_assistant::rewrite::rewrite(&w.calls, "  merci  ", "translate", "French").expect("a rewrite");
    assert_eq!(done, json!({"ok": true, "text": "stub text", "op": "translate"}));
    let [prompt] = sent(&w).try_into().expect("one call");
    assert!(
        prompt.text.contains("Translate it into French, preserving tone and meaning.")
            && prompt.text.ends_with("Text:\nmerci\n")
    );
    for (text, op, said) in [("", "fix", "nothing to rewrite"), ("hi", "pirate", "unknown op")] {
        let refused = mm_assistant::rewrite::rewrite(&w.calls, text, op, "English");
        assert!(matches!(refused, Err(AiFailure::Failure(Failure::Problem(p))) if p.said == said));
    }
    w.model.replies.lock().expect("replies").push(Err(ModelError::new("Claude API 400: bad", Some(400))));
    let failed = mm_assistant::rewrite::rewrite(&w.calls, "hi", "fix", "English");
    assert!(
        matches!(failed, Err(AiFailure::Failure(Failure::Problem(p))) if p.kind == Kind::Unreachable && p.said == "rewrite unavailable")
    );
}

#[test]
fn a_default_reply_is_banked_and_the_next_is_served_from_the_bank() {
    let w = world(true);
    let asked = ReplyAsked { pk: Some(3), ..ReplyAsked::default() };
    let made = w.drafting.reply(&asked).expect("a reply");
    assert_eq!(made, json!({"ok": true, "body": "stub body", "tone": "balanced", "model": "claude-opus-5-5"}));
    let [prompt] = sent(&w).try_into().expect("one call");
    assert!(prompt.text.starts_with("You are drafting a reply to an email thread, writing AS the account owner (ada@example.test).\nWrite a clear, friendly and professional reply"));
    assert_eq!(w.kept.drafts.lock().expect("bank").len(), 1);
    let banked = w.drafting.reply(&asked).expect("from the bank");
    assert_eq!(banked["model"], "claude-opus-5-5");
    assert!(sent(&w).is_empty());
    // A reply with an instruction is a one-off: it is asked for, and never banked.
    let warm = ReplyAsked { tone: Some("warm".into()), instruction: Some("Keep it short".into()), ..asked };
    assert_eq!(w.drafting.reply(&warm).expect("a reply")["tone"], "warm");
    let [prompt] = sent(&w).try_into().expect("one call");
    assert!(prompt.text.contains("Follow this instruction for the reply: \"Keep it short\"."));
    let nothing = w.drafting.reply(&ReplyAsked::default());
    assert!(matches!(nothing, Err(AiFailure::Failure(Failure::Problem(p))) if p.said == "bad request"));
}

/// What a question finds: the rows given, whatever it asked; a query that isn't one is rejected.
#[derive(Debug, Default)]
struct Index {
    rows: Vec<AskRow>,
    asked: Mutex<Vec<(String, Vec<String>)>>,
}

impl AskIndex for Index {
    fn for_asking(&self, fts_match: &str, accounts: &[String], _limit: i64) -> Result<Option<Vec<AskRow>>, Fault> {
        self.asked.lock().expect("asked").push((fts_match.to_owned(), accounts.to_vec()));
        Ok((!fts_match.contains("broken")).then(|| self.rows.clone()))
    }
}

fn row(pk: i64, key: i64, body: Option<&str>, summary: Option<&str>) -> AskRow {
    AskRow {
        pk,
        message_key: Some(key),
        uid: Some(pk + 100),
        account: ADA.into(),
        folder: Some("INBOX".into()),
        from_name: Some(String::new()),
        from_addr: Some("lin@example.test".into()),
        subject: None,
        snippet: Some("Lunch at noon on Thursday, by the river".into()),
        body_text: body.map(str::to_owned),
        date_iso: Some("2026-10-27T09:00:00+00:00".into()),
        ai_summary: summary.map(str::to_owned),
    }
}

fn clock_at(seconds: i64) -> Arc<Frozen> {
    Arc::new(Frozen(AtomicI64::new(seconds * 1_000_000)))
}

#[test]
fn a_question_searches_by_its_content_words_and_cites_only_what_it_was_shown() {
    assert_eq!(fts_match("When is lunch with Lin?"), "\"lunch\" OR \"Lin\"");
    assert_eq!(
        fts_match("\"Quoted\" e-mail, from ada@example.test... and I am x"),
        "\"Quoted\" OR \"e-mail\" OR \"ada@example.test...\""
    );
    assert_eq!(fts_match("the of a"), "");
    let shown = HashSet::from([1, 3]);
    assert_eq!(cited(Some(&json!([3, "3", 999, 1.7, "x", true, " 1 "])), &shown), vec![3, 1]);
    assert!(cited(Some(&json!({"3": 3})), &shown).is_empty());
}

#[test]
fn an_answer_reads_each_message_once_and_its_sources_are_what_it_cited() {
    let w = world(true);
    let index = Arc::new(Index {
        rows: vec![
            row(1, 10, Some("  Lunch moved to Thursday noon.  "), None),
            row(2, 10, Some("the same message, in another folder"), None),
            row(3, 30, None, Some("Lin confirms lunch.")),
            row(4, 40, Some("   "), Some("passed over: its body is there, and blank")),
        ],
        ..Index::default()
    });
    let context = Context::new(Arc::new(lunch()), Arc::new(Ada));
    let asker = Asker::new(w.calls.clone(), index.clone(), context, clock_at(NOW));
    w.model
        .replies
        .lock()
        .expect("replies")
        .push(Ok("{\"answer\": \" Thursday at noon. \", \"cited_pks\": [3, 2, 999]}".into()));
    let answered = asker.ask(None, Some("  When is lunch with Lin?  ")).expect("an answer");
    assert_eq!(
        answered,
        json!({"ok": true, "account": "all", "question": "When is lunch with Lin?", "answer": "Thursday at noon.",
               "sources": [{"key": 30, "folder": "INBOX", "uid": 103, "from": "lin@example.test",
                            "subject": "(no subject)", "snippet": "Lunch at noon on Thursday, by the river",
                            "account": ADA, "date": "2026-10-27T09:00:00+00:00"}],
               "model": "claude-opus-5-5", "generated": "2026-11-02T12:00:00+00:00"})
    );
    let [prompt] = sent(&w).try_into().expect("one call");
    assert!(prompt.text.contains(
        "[1] lin@example.test \u{2014} (no subject) (2026-10-27T09:00:00+00:00)\nLunch moved to Thursday noon.\n\n[3] lin@example.test \u{2014} (no subject) (2026-10-27T09:00:00+00:00)\nLin confirms lunch."
    ));
    assert!(!prompt.text.contains("passed over") && !prompt.text.contains("another folder"));
    assert_eq!(index.asked.lock().expect("asked")[0], ("\"lunch\" OR \"Lin\"".to_owned(), vec![ADA.to_owned()]));
}

#[test]
fn a_question_is_refused_before_any_mail_is_read_and_says_when_nothing_was_found() {
    let w = world(true);
    let context = Context::new(Arc::new(lunch()), Arc::new(Ada));
    let empty = Arc::new(Index::default());
    let asker = Asker::new(w.calls.clone(), empty.clone(), context, clock_at(NOW));
    let refused = |r: Result<Value, AiFailure>, said: &str| matches!(r, Err(AiFailure::Failure(Failure::Problem(p))) if p.kind == Kind::Refused && p.said == said);
    assert!(refused(asker.ask(Some("zed@example.test"), Some("lunch?")), "bad request"));
    assert!(refused(asker.ask(Some(ADA), Some("   ")), "question required"));
    assert!(refused(asker.ask(Some(ADA), Some("the broken one")), "bad query"));
    assert_eq!(empty.asked.lock().expect("asked").len(), 1);
    let none = asker.ask(Some(ADA), Some("lunch?")).expect("an answer");
    assert_eq!(
        (none["account"].clone(), none["answer"].clone(), none["sources"].clone()),
        (json!(ADA), json!("I couldn\u{2019}t find any messages matching that in your mail."), json!([]))
    );
    assert!(sent(&w).is_empty());
}

/// A filter's reach: three messages, unless it says "nothing"; most of the mailbox where it says "everything".
#[derive(Debug, Default)]
struct Reaches {
    apart: Mutex<Vec<Vec<String>>>,
}

impl PlanSearch for Reaches {
    fn narrows(&self, filter: &str) -> (bool, bool) {
        (!filter.is_empty() && !filter.starts_with('-'), filter.contains("in:"))
    }
    fn reach(&self, filter: &str, cap: i64, apart: &[&str], sample: usize) -> Result<Option<Reached>, Fault> {
        self.apart.lock().expect("apart").push(apart.iter().map(|a| (*a).to_owned()).collect());
        if filter.contains('"') {
            return Ok(None);
        }
        let n = if filter.contains("nothing") { 0 } else { 3 };
        let messages: Vec<(i64, String)> = (1..=n).map(|k| (k * 10, "INBOX".to_owned())).collect();
        let sample = messages.iter().take(sample).map(|(k, _)| json!({"key": k})).collect();
        let matched = if filter.contains("everything") { 100 } else { n };
        Ok(Some(Reached { count: n.min(cap) + i64::from(filter.contains("many")) * 600, messages, sample, matched }))
    }
    fn sweeps_whole_mailbox(&self, matched: i64) -> Result<bool, Fault> {
        Ok(matched >= 60)
    }
}

fn bulk(w: &World, clock: Arc<Frozen>) -> (BulkCommands, Arc<Reaches>) {
    let reaches = Arc::new(Reaches::default());
    let drawn = Arc::new(AtomicI64::new(0));
    let draw: mm_assistant::bulk::Draw =
        Arc::new(move || Ok(format!("plan-{}", drawn.fetch_add(1, Ordering::SeqCst) + 1)));
    (BulkCommands::new(w.calls.clone(), reaches.clone(), clock, Arc::new(Utc), draw), reaches)
}

fn planned(w: &World, bulk: &BulkCommands, plan: Value) -> Value {
    w.model.replies.lock().expect("replies").push(Ok(plan.to_string()));
    bulk.plan(Some(&json!("do that"))).expect("a plan")
}

#[test]
fn what_a_plan_takes_from_a_model_and_where_it_may_reach() {
    let plan = |v: Value| v.as_object().cloned().expect("an object");
    assert_eq!(verb_told(&plan(json!({"intent": "act", "verb": "delete"}))), None);
    assert_eq!(verb_told(&plan(json!({"intent": "search", "verb": "trash"}))), None);
    assert_eq!(verb_told(&plan(json!({"intent": "act", "verb": "trash"}))), Some("trash"));
    let weekend = plan(json!({"snooze_preset": "weekend"}));
    assert_eq!((preset_told(&weekend, "snooze"), preset_told(&weekend, "archive")), ("weekend", "tomorrow"));
    assert_eq!(preset_told(&plan(json!({"snooze_preset": "never"})), "snooze"), "tomorrow");
    let reach = |verb, names_folder| out_of_reach(verb, names_folder).into_iter().collect::<Vec<_>>();
    assert_eq!(reach("archive", false), ["all", "archive", "drafts", "junk", "sent", "trash"]);
    assert_eq!((reach("trash", true), reach("read", true)), (vec!["trash"], vec![]));
}

#[test]
fn a_command_is_previewed_as_a_plan_that_is_carried_out_once_within_its_time() {
    let w = world(true);
    let clock = clock_at(NOW);
    let (bulk, reaches) = bulk(&w, clock.clone());
    let preview = planned(&w, &bulk, json!({"intent": "act", "verb": "trash", "filter": " from:lin "}));
    assert_eq!(
        preview,
        json!({"ok": true, "intent": "act", "verb": "trash", "filter": "from:lin", "snooze_preset": "tomorrow",
               "count": 3, "cap": 200, "capped": false, "plan": "plan-1",
               "sample": [{"key": 10}, {"key": 20}, {"key": 30}]})
    );
    assert_eq!(reaches.apart.lock().expect("apart")[0], ["drafts", "junk", "sent", "trash"]);
    let [prompt] = sent(&w).try_into().expect("one call");
    assert!(prompt.text.contains("2026-11-02") && prompt.text.contains("do that"));
    let taken = bulk.take("plan-1").expect("the plan");
    assert_eq!((taken.verb.as_str(), taken.messages.len()), ("trash", 3));
    assert_eq!(bulk.take("plan-1"), None);
    assert_eq!(bulk.take("plan-9"), None);
    // Ten minutes on, a plan has expired.
    let snooze =
        planned(&w, &bulk, json!({"intent": "act", "verb": "snooze", "filter": "many", "snooze_preset": "weekend"}));
    assert_eq!((snooze["snooze_preset"].clone(), snooze["capped"].clone()), (json!("weekend"), json!(true)));
    clock.0.fetch_add(600 * 1_000_000, Ordering::SeqCst);
    assert_eq!(bulk.take("plan-2"), None);
}

#[test]
fn a_command_that_isnt_a_clean_scoped_act_is_a_search() {
    let w = world(true);
    let (bulk, _) = bulk(&w, clock_at(NOW));
    let search = |filter: &str| json!({"ok": true, "intent": "search", "filter": filter});
    for (plan, filter) in [
        (json!({"intent": "search", "filter": "lunch"}), "lunch"),
        (json!({"intent": "act", "verb": "delete", "filter": "from:lin"}), "from:lin"),
        (json!({"intent": "act", "verb": "archive", "filter": ""}), ""),
        (json!({"intent": "act", "verb": "archive", "filter": "-from:lin"}), "-from:lin"),
        (json!({"intent": "act", "verb": "archive", "filter": "\"unbalanced"}), "\"unbalanced"),
        (json!({"intent": "act", "verb": "archive", "filter": "everything"}), "everything"),
    ] {
        assert_eq!(planned(&w, &bulk, plan), search(filter));
    }
    let nothing = planned(&w, &bulk, json!({"intent": "act", "verb": "read", "filter": "nothing"}));
    assert_eq!(nothing, json!({"ok": true, "intent": "search", "filter": "nothing", "empty": true}));
    let empty = bulk.plan(Some(&json!("   ")));
    assert!(matches!(empty, Err(AiFailure::Failure(Failure::Problem(p))) if p.said == "empty command"));
    w.model.replies.lock().expect("replies").push(Err(ModelError::new("Claude API 400: bad", Some(400))));
    let failed = bulk.plan(Some(&json!("archive Lin")));
    assert!(
        matches!(failed, Err(AiFailure::Failure(Failure::Problem(p))) if p.kind == Kind::Unreachable && p.said == "planner unavailable")
    );
}

#[test]
fn only_the_newest_plans_are_kept() {
    let w = world(true);
    let (bulk, _) = bulk(&w, clock_at(NOW));
    for _ in 0..17 {
        planned(&w, &bulk, json!({"intent": "act", "verb": "star", "filter": "from:lin"}));
    }
    assert_eq!(bulk.take("plan-1"), None);
    assert!(bulk.take("plan-2").is_some() && bulk.take("plan-17").is_some());
}
