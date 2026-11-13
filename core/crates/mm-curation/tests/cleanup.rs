//! A sender's cleanup and its unsubscribe, held to the Python app's answers (tests/unit/curation/test_cleanup_plan.py,
//! test_unsubscribe_attempt.py, test_unsub_capture.py and test_batch_cleanup.py): what a cleanup is before anything
//! happens, what trying a way out comes to, where the way comes from, and that a way this server can't send yet
//! changes nothing. On fakes behind Curation's ports, with no database and no I/O.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use mm_curation::cleaning::{
    Asked, BATCH_MAX, BATCH_SWEEP_CAP, Cleaning, CleaningParts, SWEEP_CAP, Tally, in_date, plan_from, reversible,
    sender_named, senders_named, tally, within_batch, within_cap,
};
use mm_curation::cleanup_ports::{
    Audited, CleanupFiling, CleanupMail, ConfirmTokens, Ids, ListHeaders, NamedCopy, SafeHttp, StoredWay, SweepTrail,
    SystemSend, Target, UnsubRow,
};
use mm_curation::ports::{Classified, InboxSender, NoisySender, Reclassified, RuleBook, SenderMail};
use mm_curation::rulebook::Rules;
use mm_curation::unsubscribe::{
    self, Attempt, Logged, Mailto, UnsubWay, Unsubscribing, UnsubscribingParts, after_mailto, after_one_click,
    attempt_key, list_unsub, mail_date, mailto_message, mailto_of, not_tried, parse_list_unsub, preview, preview_said,
    read_only, repeat_of, said, without_sending,
};
use mm_kernel::errors::{Failure, Fault, NOT_YET};
use mm_kernel::seams::{Clock, Moment};
use serde_json::{Value, json};

const LOOM: &str = "news@loom.example.test";
const ADA: &str = "ada@example.test";
const BEA: &str = "bea@example.test";
const HEADERS: &[u8] = b"List-Unsubscribe: <https://loom.example.test/u/ada-218>\r\n\
List-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n\r\n";

fn one_click() -> UnsubWay {
    list_unsub("<https://loom.example.test/u/ada-218>", "List-Unsubscribe=One-Click")
}

fn page() -> UnsubWay {
    UnsubWay { method: "http-page", https_uri: Some("https://list.example.test/leave?t=1".into()), ..UnsubWay::none() }
}

fn mailto() -> UnsubWay {
    UnsubWay { method: "mailto", mailto_to: Some("leave@list.example.test".into()), ..UnsubWay::none() }
}

fn attempt(status: &str, method: &str) -> Attempt {
    Attempt { status: status.into(), method: method.into(), http_status: None, browser_url: None }
}

// ── what a cleanup is before anything happens ────────────────────────────

#[test]
fn a_request_names_a_sender_by_address() {
    for (raw, addr) in [
        (Some("  Ada@Example.TEST "), Some("ada@example.test")),
        (Some("ada@example.test"), Some("ada@example.test")),
        (Some("example.test"), None),
        (Some(""), None),
        (Some("   "), None),
        (None, None),
    ] {
        assert_eq!(sender_named(raw).as_deref(), addr, "{raw:?}");
    }
}

#[test]
fn a_plan_blocks_the_sender_unless_it_asks_for_the_domain() {
    let asked = |addr: &str, scope: Option<&str>| Asked {
        from_addr: Some(addr.into()),
        block_scope: scope.map(str::to_owned),
        ..Asked::default()
    };
    assert_eq!(plan_from(&asked("example.test", Some("domain"))), None);
    assert_eq!(plan_from(&Asked::default()), None);
    assert_eq!(plan_from(&asked("Ada@Example.test", None)).expect("plan").block_token(), "ada@example.test");
    let domain = plan_from(&asked("ada@sub.example.test", Some("domain"))).expect("plan");
    assert_eq!((domain.scope, domain.block_token().as_str()), ("domain", "sub.example.test"));
    let plan = plan_from(&asked("ada@example.test", None)).expect("plan");
    assert_eq!((plan.scope, plan.do_unsubscribe, plan.confirm_token.as_str()), ("sender", false, ""));
}

#[test]
fn block_and_sweep_can_be_undone_and_the_unsubscribe_cannot() {
    assert_eq!(reversible(), json!({"block": true, "sweep": true, "unsubscribe": false}));
}

#[test]
fn the_sweep_takes_up_to_the_cap_and_says_when_it_left_some() {
    for (read, kept, capped) in [
        (0, 0, false),
        (SWEEP_CAP - 1, SWEEP_CAP - 1, false),
        (SWEEP_CAP, SWEEP_CAP, false),
        (SWEEP_CAP + 1, SWEEP_CAP, true),
    ] {
        let (taken, hit) = within_cap((0..read).collect::<Vec<_>>());
        assert_eq!((taken.len(), hit), (kept, capped), "{read}");
    }
}

#[test]
fn each_answer_counts_once_and_a_queued_one_counts_as_swept() {
    let got = tally([
        json!({"ok": true}),
        json!({"ok": true, "status": "waiting"}),
        json!({"ok": true, "status": "queued"}),
        json!({"status": "queued"}),
        json!({"ok": false}),
        json!({}),
        Value::Null,
    ]);
    assert_eq!(got, Tally { moved: 2, queued: 2, failed: 3 });
    assert_eq!(Tally { moved: 2, queued: 3, failed: 4 }.swept(), 5);
    assert_eq!(tally([]), Tally::default());
}

#[test]
fn a_batch_names_distinct_senders_and_no_more_than_it_shows() {
    let named = |raw: &[&str]| senders_named(Some(&raw.iter().map(|s| (*s).to_owned()).collect::<Vec<_>>()));
    assert_eq!(
        named(&["A@example.test", "a@example.test", "b@example.test"]),
        Some(vec!["a@example.test".into(), "b@example.test".into()])
    );
    assert_eq!(named(&[]), None);
    assert_eq!(named(&["a@example.test", "example.test"]), None);
    assert_eq!(senders_named(None), None);
    let many: Vec<String> = (0..=BATCH_MAX).map(|n| format!("s{n}@example.test")).collect();
    assert_eq!(senders_named(Some(&many)), None);
    assert!(senders_named(Some(&many[..BATCH_MAX])).is_some());
}

#[test]
fn a_batchs_moves_are_shared_out_in_the_order_the_senders_came() {
    let read = vec![
        ("a".to_owned(), vec![0; BATCH_SWEEP_CAP - 10], false),
        ("b".to_owned(), vec![0; 30], false),
        ("c".to_owned(), vec![0; 5], false),
    ];
    let (taken, capped) = within_batch(read);
    let sizes: Vec<(String, usize)> = taken.into_iter().map(|(a, t)| (a, t.len())).collect();
    assert_eq!(sizes, [("a".into(), BATCH_SWEEP_CAP - 10), ("b".into(), 10), ("c".into(), 0)]);
    assert!(capped);
    let (_, capped) = within_batch(vec![("a".to_owned(), vec![0; 3], true)]);
    assert!(capped, "a sender's own cap counts");
}

#[test]
fn a_token_in_date_is_digits_and_a_dot_of_the_last_ten_minutes() {
    let now = 1_800_000_600;
    assert!(in_date("1800000000.abc", now, 600));
    assert!(in_date("1800000600", now, 600));
    assert!(!in_date("1799999999.abc", now, 600));
    assert!(!in_date("1800000601.abc", now, 600), "one from the future");
    for token in ["", ".abc", "not.a-token", "\u{663}.abc", "+1800000000.abc", "1234567890123.abc"] {
        assert!(!in_date(token, now, 600), "{token:?}");
    }
}

// ── what a message says of the way out ───────────────────────────────────

#[test]
fn a_url_folded_in_the_middle_comes_back_whole() {
    // RFC 2369 says whitespace within one is ignored; a space or a line break left in it can't be requested.
    let r = parse_list_unsub(
        b"List-Unsubscribe: <https://list.example.test/u?t=abc\r\n def>,\r\n \
          <mailto:out@list.example.test?subject=\r\n stop>\r\n\r\nHello.\r\n",
    );
    assert_eq!(r.https_uri.as_deref(), Some("https://list.example.test/u?t=abcdef"));
    assert_eq!(r.mailto_to.as_deref(), Some("out@list.example.test"));
    assert_eq!(r.mailto_subject.as_deref(), Some("stop"));
}

#[test]
fn one_click_wants_rfc_8058s_one_spelling_and_a_bare_https_is_a_page() {
    assert_eq!(one_click().method, "one-click");
    assert_eq!(list_unsub("<https://loom.example.test/u>", "List=One-Click").method, "http-page");
    assert_eq!(list_unsub("<https://loom.example.test/u>, <mailto:u@loom.example.test>", "").method, "mailto");
    assert_eq!(list_unsub("<http://loom.example.test/u>", "List-Unsubscribe=One-Click").method, "none");
    assert_eq!(list_unsub("", "").method, "none");
    let stored = unsubscribe::list_unsub_stored(&one_click().raw);
    assert_eq!(stored, one_click(), "what is kept reads back the same");
}

// ── what trying a way out comes to ───────────────────────────────────────

#[test]
fn only_what_got_somewhere_is_latched() {
    for (tried, latched) in [
        (after_one_click(true, Some(200)), true),
        (after_one_click(false, Some(500)), false),
        (after_mailto(true), true),
        (after_mailto(false), false),
        (without_sending(&page()), true),
        (without_sending(&UnsubWay::none()), false),
        (read_only(), false),
        (not_tried(true), false),
        (not_tried(false), false),
    ] {
        assert_eq!(tried.latched(), latched, "{tried:?}");
    }
    assert_eq!(attempt_key(ADA), "unsub:ada@example.test");
}

#[test]
fn each_way_says_what_it_came_to() {
    assert_eq!(
        after_one_click(true, Some(200)),
        Attempt { http_status: Some(200), ..attempt("unsubscribed_oneclick", "one-click") }
    );
    assert_eq!(after_one_click(false, None), attempt("failed", "one-click"));
    assert_eq!(after_mailto(true), attempt("mailto_sent", "mailto"));
    assert_eq!(after_mailto(false), attempt("failed", "mailto"));
    assert_eq!(
        without_sending(&page()),
        Attempt { browser_url: page().https_uri, ..attempt("needs_browser", "http-page") }
    );
    assert_eq!(without_sending(&UnsubWay::none()), attempt("no_method", "none"));
    assert_eq!(read_only(), attempt("read_only", "none"));
    assert_eq!(not_tried(true), attempt("no_token", "none"));
    assert_eq!(not_tried(false), attempt("skipped", "none"));
}

#[test]
fn a_repeat_answers_what_it_came_to_and_what_isnt_latched_is_tried_again() {
    let logged = |status: &str, method: &str| Logged { status: status.into(), method: method.into() };
    assert_eq!(repeat_of(None, &one_click()), None);
    assert_eq!(repeat_of(Some(&logged("failed", "one-click")), &one_click()), None);
    assert_eq!(repeat_of(Some(&logged("mailto_sent", "mailto")), &page()), Some(attempt("mailto_sent", "mailto")));
    assert_eq!(
        repeat_of(Some(&logged("needs_browser", "http-page")), &page()),
        Some(Attempt { browser_url: page().https_uri, ..attempt("needs_browser", "http-page") })
    );
}

#[test]
fn a_preview_shows_the_way_and_only_where_it_goes() {
    assert_eq!(preview(&one_click()), json!({"method": "one-click", "https_host": "loom.example.test"}));
    assert_eq!(preview(&page()), json!({"method": "http-page", "https_host": "list.example.test"}));
    assert_eq!(preview(&UnsubWay { https_uri: None, ..page() }), json!({"method": "http-page", "https_host": null}));
    assert_eq!(preview(&mailto()), json!({"method": "mailto", "mailto_to": "leave@list.example.test"}));
    assert_eq!(preview(&UnsubWay::none()), json!({"method": "none"}));
    assert_eq!(
        preview_said("Ada@Example.test", &one_click()),
        [Some(ADA.to_owned()), Some("one-click".to_owned()), one_click().https_uri]
    );
    assert_eq!(preview_said(ADA, &mailto())[2].as_deref(), Some("leave@list.example.test"));
}

#[test]
fn no_way_out_reads_one_way_however_a_message_said_it() {
    let unusable = UnsubWay { raw: "something unusable".into(), ..UnsubWay::none() };
    assert!(!unusable.offers_a_way());
    assert_eq!(said(unusable), UnsubWay::none());
    assert_eq!(said(one_click()), one_click());
}

#[test]
fn a_mailto_goes_to_one_plain_address_or_not_at_all() {
    for to in [
        "",
        "no-at-sign",
        "two@at@example.test",
        "a b@example.test",
        "a@example.test,b@example.test",
        "a@example.test\r\nBcc: b@example.test",
        "<a@example.test>",
        "a\"@example.test",
        "a@",
        "@example.test",
        "a:b@example.test",
        "a;b@example.test",
        "(a)@example.test",
        "[a]@example.test",
        "a\\b@example.test",
    ] {
        assert_eq!(mailto_of(&UnsubWay { mailto_to: Some(to.into()), ..mailto() }), None, "{to:?}");
    }
    assert_eq!(mailto_of(&UnsubWay { mailto_to: None, ..mailto() }), None);
    assert_eq!(
        mailto_of(&mailto()),
        Some(Mailto {
            to: "leave@list.example.test".into(),
            subject: "unsubscribe".into(),
            text: "Please unsubscribe me.".into()
        })
    );
}

#[test]
fn a_mailtos_subject_stays_on_one_line_and_its_text_keeps_one_paragraph_break() {
    let with = |subject: Option<&str>, body: Option<&str>| {
        mailto_of(&UnsubWay {
            mailto_subject: subject.map(str::to_owned),
            mailto_body: body.map(str::to_owned),
            ..mailto()
        })
        .expect("mailto")
    };
    assert_eq!(with(Some("stop\r\nBcc: a@example.test"), None).subject, "stop  Bcc: a@example.test");
    assert_eq!(with(Some(&"x".repeat(500)), None).subject.len(), 200);
    assert_eq!(with(None, Some("a\r\n\r\n\r\nb\n\nc\nd")).text, "a\n\nb\n\nc\nd");
    assert_eq!(with(None, Some(&"x".repeat(5000))).text.len(), 1000);
}

#[test]
fn a_mailto_goes_as_python_writes_it_with_crlf() {
    let to = Mailto {
        to: "unsubscribe@loom.example.test".into(),
        subject: "unsubscribe ada".into(),
        text: "Please unsubscribe me.".into(),
    };
    let date = mail_date(Moment::from_micros(1_793_620_800_000_000));
    assert_eq!(date, "Mon, 02 Nov 2026 12:00:00 +0000");
    let raw = mailto_message(ADA, &to, &date, "<1.ab@example.test>");
    assert_eq!(
        String::from_utf8(raw).expect("utf-8"),
        "From: ada@example.test\r\nTo: unsubscribe@loom.example.test\r\nSubject: unsubscribe ada\r\n\
         Date: Mon, 02 Nov 2026 12:00:00 +0000\r\nMessage-ID: <1.ab@example.test>\r\nAuto-Submitted: auto-generated\r\n\
         Content-Type: text/plain; charset=\"utf-8\"\r\nContent-Transfer-Encoding: 7bit\r\nMIME-Version: 1.0\r\n\r\n\
         Please unsubscribe me.\r\n"
    );
    let non_ascii = Mailto { subject: "désabonner".into(), text: "Merci.\nÀ bientôt".into(), ..to };
    let raw = String::from_utf8(mailto_message(ADA, &non_ascii, &date, "<1.ab@example.test>")).expect("utf-8");
    assert!(raw.contains("Subject: =?utf-8?b?ZMOpc2Fib25uZXI=?=\r\n"), "{raw}");
    assert!(raw.contains("Content-Transfer-Encoding: 8bit\r\n"));
    assert!(raw.ends_with("Merci.\r\nÀ bientôt\r\n"));
}

// ── where the way out comes from, and what a cleanup does ────────────────

/// Every port a cleanup asks, each call noted.
#[derive(Debug, Default)]
struct World {
    rows: Vec<UnsubRow>,
    stored: Option<UnsubWay>,
    kept: Option<(String, Vec<String>)>,
    heard: Option<Vec<u8>>,
    logged: Mutex<HashMap<String, Logged>>,
    targets: Vec<Target>,
    rules: Mutex<Rules>,
    can_send: bool,
    said: Mutex<Vec<String>>,
}

impl World {
    fn note(&self, what: String) {
        self.said.lock().expect("lock").push(what);
    }

    fn said(&self) -> Vec<String> {
        self.said.lock().expect("lock").clone()
    }
}

fn row(pk: i64, uid: i64) -> UnsubRow {
    UnsubRow {
        pk,
        account: ADA.into(),
        folder: "INBOX".into(),
        uid: Some(uid),
        list_unsub_raw: None,
        list_unsub_method: None,
    }
}

impl CleanupMail for World {
    fn counts(&self, _: &str) -> Result<(i64, i64), Fault> {
        Ok((i64::try_from(self.targets.len()).unwrap_or(0), 1))
    }
    fn sweep_targets(&self, _: &str, limit: usize) -> Result<Vec<Target>, Fault> {
        Ok(self.targets.iter().take(limit).cloned().collect())
    }
    fn unsub_stored(&self, _: &str) -> Result<StoredWay, Fault> {
        Ok((self.rows.clone(), vec![ADA.into()], self.stored.clone()))
    }
    fn remember_unsub(&self, pk: i64, parsed: &UnsubWay) -> Result<(), Fault> {
        self.note(format!("remember {pk} {}", parsed.method));
        Ok(())
    }
    fn keep_unsub(&self, addr: &str, info: &UnsubWay, accounts: &[String]) -> Result<(), Fault> {
        self.note(format!("keep {addr} {} {accounts:?}", info.method));
        Ok(())
    }
    fn kept_unsub(&self, _: &str) -> Result<Option<(String, Vec<String>)>, Fault> {
        Ok(self.kept.clone())
    }
    fn attempt(&self, key: &str) -> Result<Option<Logged>, Fault> {
        Ok(self.logged.lock().expect("lock").get(key).cloned())
    }
    fn record_attempt(&self, _: &str, tried: &Attempt, key: &str, _: &[String]) -> Result<(), Fault> {
        self.note(format!("latch {}", tried.status));
        self.logged
            .lock()
            .expect("lock")
            .entry(key.into())
            .or_insert(Logged { status: tried.status.clone(), method: tried.method.clone() });
        Ok(())
    }
}

impl ListHeaders for World {
    fn read(&self, row: &UnsubRow) -> Option<Vec<u8>> {
        self.note(format!("read {}", row.pk));
        self.heard.clone()
    }
}

impl SafeHttp for World {
    fn ready(&self) -> bool {
        self.can_send
    }
    fn one_click_post(&self, url: &str) -> (bool, Option<i64>) {
        self.note(format!("post {url}"));
        (true, Some(200))
    }
}

impl SystemSend for World {
    fn ready(&self) -> bool {
        self.can_send
    }
    fn send(&self, account: &str, _: &[u8], _: &str, to: &str, _: &str, _: &str) -> Result<(), Fault> {
        self.note(format!("send {account} {to}"));
        Ok(())
    }
}

impl Ids for World {
    fn hex(&self, n: usize) -> String {
        "0".repeat(n * 2)
    }
}

impl Clock for World {
    fn now(&self) -> Moment {
        Moment::from_micros(1_800_000_000_000_000)
    }
}

impl RuleBook for World {
    fn load(&self) -> Result<Rules, Fault> {
        Ok(self.rules.lock().expect("lock").clone())
    }
    fn change(&self, apply: &mut dyn FnMut(&mut Rules) -> Result<(), Failure>) -> Result<(), Failure> {
        self.note("rules changed".into());
        apply(&mut self.rules.lock().expect("lock"))
    }
}

impl SenderMail for World {
    fn inbox_senders(&self, _: &str) -> Result<Vec<InboxSender>, Fault> {
        Ok(Vec::new())
    }
    fn classified_from(&self, _: &str) -> Result<Vec<Classified>, Fault> {
        Ok(Vec::new())
    }
    fn set_classifications(&self, _: &[Reclassified]) -> Result<(), Fault> {
        Ok(())
    }
    fn noisy_senders(&self, _: &[&str], _: i64) -> Result<Vec<NoisySender>, Fault> {
        Ok(Vec::new())
    }
    fn accounts_by_sender(&self, _: &[String]) -> Result<HashMap<String, Vec<(String, i64)>>, Fault> {
        Ok(HashMap::new())
    }
}

impl CleanupFiling for World {
    fn trash(&self, pk: i64, group: &str) -> Result<Value, Failure> {
        self.note(format!("trash {pk} {group}"));
        Ok(json!({"ok": true}))
    }
    fn trash_many(&self, rows: &[NamedCopy], group: &str) -> Result<Value, Failure> {
        self.note(format!("trash many {} {group}", rows.len()));
        let filed: Vec<Value> =
            rows.iter().map(|(key, folder, uid)| json!({"key": key, "folder": folder, "uid": uid})).collect();
        Ok(json!({"count": rows.len(), "failed": 0, "queued": 0, "filed": filed}))
    }
    fn undo_push(&self, entry: &Value, group: &str) -> Result<(), Fault> {
        self.note(format!("undo {} {} {group}", entry["kind"], entry["token"]));
        Ok(())
    }
    fn stats_changed(&self) {}
}

impl SweepTrail for World {
    fn swept(&self, _: &str, _: &[Audited]) -> Result<(), Fault> {
        Ok(())
    }
    fn cleaned_up(&self, from: &str, _: &str, swept: usize, status: &str) -> Result<(), Fault> {
        self.note(format!("cleaned up {from} {swept} {status}"));
        Ok(())
    }
}

/// Tokens that name what they were minted for, in plain sight.
#[derive(Debug)]
struct PlainTokens;

impl ConfirmTokens for PlainTokens {
    fn mint(&self, purpose: &str, subject: &[Option<String>], now: i64) -> String {
        format!("{now}.{purpose}:{subject:?}")
    }
    fn names(&self, token: &str, purpose: &str, subject: &[Option<String>]) -> bool {
        token.split_once('.').is_some_and(|(_, said)| said == format!("{purpose}:{subject:?}"))
    }
}

fn unsubscribing(world: &Arc<World>, writes_allowed: bool) -> Unsubscribing {
    Unsubscribing::new(UnsubscribingParts {
        mail: world.clone(),
        headers: world.clone(),
        http: world.clone(),
        send: world.clone(),
        ids: world.clone(),
        clock: world.clone(),
        writes_allowed,
    })
}

fn cleaning(world: &Arc<World>) -> Cleaning {
    Cleaning::new(CleaningParts {
        rules: world.clone(),
        classified: world.clone(),
        mail: world.clone(),
        unsubscribing: unsubscribing(world, true),
        filing: world.clone(),
        audit: world.clone(),
        tokens: Arc::new(PlainTokens),
        ids: world.clone(),
        clock: world.clone(),
    })
}

fn newest_and_older() -> Vec<UnsubRow> {
    vec![row(9, 19), row(4, 14)]
}

#[test]
fn what_ingest_kept_answers_and_the_server_isnt_asked() {
    let world = Arc::new(World { rows: newest_and_older(), stored: Some(one_click()), ..World::default() });
    assert_eq!(unsubscribing(&world, true).resolve(LOOM), Ok((one_click(), vec![ADA.to_owned()])));
    assert_eq!(world.said(), [format!("keep {LOOM} one-click [\"{ADA}\"]")]);
}

#[test]
fn older_mail_has_the_newest_read_off_the_server_and_remembered() {
    let world = Arc::new(World { rows: newest_and_older(), heard: Some(HEADERS.to_vec()), ..World::default() });
    let (info, accounts) = unsubscribing(&world, true).resolve(LOOM).expect("resolved");
    assert_eq!((info.method, info.https_uri.as_deref()), ("one-click", Some("https://loom.example.test/u/ada-218")));
    assert_eq!(accounts, [ADA]);
    assert_eq!(
        world.said(),
        ["read 9".to_owned(), "remember 9 one-click".into(), format!("keep {LOOM} one-click [\"{ADA}\"]")]
    );
}

#[test]
fn with_the_server_unable_to_say_no_way_is_offered_and_nothing_is_remembered() {
    let world = Arc::new(World { rows: newest_and_older(), ..World::default() });
    assert_eq!(unsubscribing(&world, true).resolve(LOOM), Ok((UnsubWay::none(), vec![ADA.to_owned()])));
    assert_eq!(world.said(), ["read 9"]);
}

#[test]
fn with_none_of_the_senders_mail_left_what_was_last_read_still_knows_the_way() {
    let world = Arc::new(World { kept: Some((one_click().raw, vec![BEA.into()])), ..World::default() });
    assert_eq!(unsubscribing(&world, true).resolve(LOOM), Ok((one_click(), vec![BEA.to_owned()])));
    assert!(world.said().is_empty());
}

#[test]
fn a_newest_message_offering_no_way_is_taken_at_its_word_over_what_was_kept() {
    let world = Arc::new(World {
        rows: newest_and_older(),
        stored: Some(UnsubWay::none()),
        kept: Some((one_click().raw, vec![ADA.into()])),
        ..World::default()
    });
    assert_eq!(unsubscribing(&world, true).resolve(LOOM), Ok((UnsubWay::none(), vec![ADA.to_owned()])));
    assert_eq!(world.said(), [format!("keep {LOOM} none [\"{ADA}\"]")]);
}

fn target(pk: i64) -> Target {
    Target {
        pk,
        key: Some(pk + 100),
        uid: Some(pk),
        account: ADA.into(),
        folder: "INBOX".into(),
        subject: None,
        seen: Some(0),
    }
}

fn confirmed(world: &Arc<World>) -> Asked {
    let token =
        cleaning(world).sender(Some(LOOM)).expect("preview")["confirm_token"].as_str().expect("token").to_owned();
    world.said.lock().expect("lock").clear();
    Asked { from_addr: Some(LOOM.into()), do_unsubscribe: true, confirm_token: Some(token), ..Asked::default() }
}

#[test]
fn a_way_this_server_cant_send_yet_changes_nothing() {
    let world = Arc::new(World {
        rows: newest_and_older(),
        stored: Some(one_click()),
        targets: vec![target(9)],
        ..World::default()
    });
    let asked = confirmed(&world);
    assert_eq!(cleaning(&world).execute(&asked), Err(Failure::Fault(NOT_YET)));
    assert!(world.said().iter().all(|s| s.starts_with("keep ")), "{:?}", world.said());
    assert!(world.rules.lock().expect("lock").list("block_senders").is_empty());
}

#[test]
fn a_cleanup_blocks_sweeps_then_unsubscribes_and_a_repeat_fires_nothing() {
    let world = Arc::new(World {
        rows: newest_and_older(),
        stored: Some(one_click()),
        targets: vec![target(9), target(4)],
        can_send: true,
        ..World::default()
    });
    let asked = confirmed(&world);
    let done = cleaning(&world).execute(&asked).expect("cleaned up");
    assert_eq!(
        (done["status"].clone(), done["swept"].clone(), done["blocked_token"].clone()),
        (json!("unsubscribed_oneclick"), json!(2), json!(LOOM))
    );
    let group = "cleanup:00000000000000000000000000000000";
    assert_eq!(
        world.said(),
        [
            format!("keep {LOOM} one-click [\"{ADA}\"]"),
            "rules changed".into(),
            format!("undo \"unblock\" \"{LOOM}\" {group}"),
            format!("trash 9 {group}"),
            format!("trash 4 {group}"),
            "post https://loom.example.test/u/ada-218".into(),
            "latch unsubscribed_oneclick".into(),
            format!("cleaned up {LOOM} 2 unsubscribed_oneclick"),
        ]
    );
    assert_eq!(world.rules.lock().expect("lock").list("block_senders"), [LOOM]);
    let again = cleaning(&world).execute(&asked).expect("again");
    assert_eq!(again["status"], json!("unsubscribed_oneclick"));
    assert_eq!(world.said().iter().filter(|s| s.starts_with("post ")).count(), 1, "a repeat never fires again");
}

#[test]
fn without_a_good_token_the_block_and_sweep_stand_and_nothing_leaves() {
    let world = Arc::new(World {
        rows: newest_and_older(),
        stored: Some(one_click()),
        targets: vec![target(9)],
        ..World::default()
    });
    for token in [None, Some("1800000000.another sender's"), Some("1700000000.cleanup")] {
        let asked = Asked {
            from_addr: Some(LOOM.into()),
            do_unsubscribe: true,
            confirm_token: token.map(str::to_owned),
            ..Asked::default()
        };
        let done = cleaning(&world).execute(&asked).expect("cleaned up");
        assert_eq!((done["status"].clone(), done["swept"].clone()), (json!("no_token"), json!(1)), "{token:?}");
    }
    assert!(!world.said().iter().any(|s| s.starts_with("post ")));
}

#[test]
fn a_read_only_process_blocks_and_sweeps_and_sends_nothing() {
    let world = Arc::new(World {
        rows: newest_and_older(),
        stored: Some(one_click()),
        targets: vec![target(9)],
        ..World::default()
    });
    let asked = confirmed(&world);
    let read_only = Cleaning::new(CleaningParts {
        rules: world.clone(),
        classified: world.clone(),
        mail: world.clone(),
        unsubscribing: unsubscribing(&world, false),
        filing: world.clone(),
        audit: world.clone(),
        tokens: Arc::new(PlainTokens),
        ids: world.clone(),
        clock: world.clone(),
    });
    let done = read_only.execute(&asked).expect("cleaned up");
    assert_eq!((done["status"].clone(), done["swept"].clone()), (json!("read_only"), json!(1)));
}

#[test]
fn a_batch_blocks_each_sender_and_trashes_every_copy_under_one_group() {
    let world = Arc::new(World { targets: vec![target(9), target(4)], ..World::default() });
    let senders = vec![LOOM.to_owned(), "deals@spindle.example.test".to_owned()];
    let done = cleaning(&world).batch_execute(Some(&senders), Some("cleanup:batch")).expect("batch");
    assert_eq!(done["blocked"], json!(senders));
    // Both senders' lists hold the same two copies here: each is trashed once, and counts for the later sender.
    assert_eq!(done["swept"], json!(2));
    let said = world.said();
    assert_eq!(said.iter().filter(|s| s.starts_with("undo \"unblock\"")).count(), 2);
    assert!(said.contains(&"trash many 2 cleanup:batch".to_owned()));
    assert!(said.contains(&format!("cleaned up {LOOM} 0 skipped")));
    assert!(said.contains(&"cleaned up deals@spindle.example.test 2 skipped".to_owned()));
}

#[test]
fn a_sender_with_no_mail_or_no_address_is_refused() {
    let world = Arc::new(World::default());
    let nobody = cleaning(&world).batch_preview(Some(&[LOOM.to_owned()]));
    assert!(matches!(nobody, Err(Failure::Problem(p)) if p.said == "no such sender"));
    let nobody = cleaning(&world).sender(Some(LOOM));
    assert!(matches!(nobody, Err(Failure::Problem(p)) if p.said == "no such sender"));
    let bad = cleaning(&world).sender(Some("not-an-address"));
    assert!(matches!(bad, Err(Failure::Problem(p)) if p.said == "bad address"));
}
