//! Expiry, held to the Python app's answers (tests/unit/curation/test_expire.py and test_expiry_rounds.py): which
//! rule covers a sender, which mail is past its age, what is read of a message, then one account's pass, its turn on
//! the gate and the background round, on fakes behind Curation's ports.

#![allow(clippy::unwrap_used)]

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::sync::{Arc, Mutex};

use mm_curation::cleanup_ports::{
    Audited, LiveError, LiveInboxes, LiveSession, NamedCopy, SweepTrail, SweptAccount, SweptRows,
};
use mm_curation::expiry::{
    AGE_FIELDS, Aged, Expired, Expiry, ExpiryParts, aged_from_headers, audited, expiry_hours, select_expired,
};
use mm_curation::ports::{Filing, RuleBook};
use mm_curation::rulebook::Rules;
use mm_kernel::errors::{Failure, Fault};
use mm_kernel::seams::{Clock, Moment};
use serde_json::{Value, json};

const HOUR: i64 = 3600;
const NOW: i64 = 1_800_000_000;
/// 2026-01-01 12:00 UTC.
const NEW_YEAR_NOON: i64 = 1_767_268_800;
/// 2020-01-01.
const OLD: i64 = 1_577_836_800;
const ADA: &str = "ada@example.test";
const BEA: &str = "bea@example.test";

fn rules(pairs: &[(&str, i64)]) -> Vec<(String, i64)> {
    pairs.iter().map(|(t, h)| ((*t).to_owned(), *h)).collect()
}

// -- matching ----------------------------------------------------------------------------------------------------

#[test]
fn a_domain_covers_itself_and_its_sub_domains() {
    for addr in ["bot@notify-example.test", "bot@mail.notify-example.test", "Bot@Notify-Example.TEST"] {
        assert_eq!(expiry_hours(addr, &rules(&[("notify-example.test", 72)])), Some(72), "{addr}");
    }
}

#[test]
fn a_domain_covers_no_lookalike() {
    for addr in ["bot@evilnotify-example.test", "notify-example.test@other.test"] {
        assert_eq!(expiry_hours(addr, &rules(&[("notify-example.test", 72)])), None, "{addr}");
    }
}

#[test]
fn a_senders_entry_wins_over_its_domains() {
    let expire = rules(&[("notify-example.test", 72), ("alerts@notify-example.test", 240)]);
    assert_eq!(expiry_hours("alerts@notify-example.test", &expire), Some(240));
    assert_eq!(expiry_hours("other@notify-example.test", &expire), Some(72));
}

#[test]
fn of_the_domains_that_cover_a_sender_the_shortest_life_wins() {
    let expire = rules(&[("example.test", 240), ("notify.example.test", 24), ("mail.notify.example.test", 72)]);
    assert_eq!(expiry_hours("bot@mail.notify.example.test", &expire), Some(24));
}

#[test]
fn with_no_rule_nothing_expires() {
    for addr in ["someone@person.test", ""] {
        assert_eq!(expiry_hours(addr, &rules(&[("notify-example.test", 72)])), None, "{addr:?}");
    }
}

// -- age selection -----------------------------------------------------------------------------------------------

fn row(addr: &str, age_hours: Option<i64>) -> Aged {
    Aged {
        uid: "1".into(),
        from: addr.into(),
        subject: String::new(),
        mid: String::new(),
        ts: age_hours.map(|h| NOW - h * HOUR),
    }
}

#[test]
fn only_mail_older_than_its_rule_is_taken() {
    let rows = vec![
        row("a@notify-example.test", Some(100)),
        row("b@notify-example.test", Some(10)),
        row("c@person.test", Some(1000)),
    ];
    let hit = select_expired(rows, &rules(&[("notify-example.test", 72)]), NOW as f64);
    assert_eq!(hit.iter().map(|e| e.aged.from.as_str()).collect::<Vec<_>>(), ["a@notify-example.test"]);
    assert_eq!(hit[0].hours, 72);
}

#[test]
fn mail_exactly_at_its_limit_is_kept() {
    let expire = rules(&[("notify-example.test", 72)]);
    assert!(select_expired(vec![row("a@notify-example.test", Some(72))], &expire, NOW as f64).is_empty());
    // A moment past it, it goes.
    assert_eq!(select_expired(vec![row("a@notify-example.test", Some(72))], &expire, NOW as f64 + 0.5).len(), 1);
}

#[test]
fn mail_of_unknown_age_never_expires() {
    let expire = rules(&[("notify-example.test", 72)]);
    assert!(select_expired(vec![row("a@notify-example.test", None)], &expire, NOW as f64).is_empty());
}

// -- what expiry reads and what the audit says ---------------------------------------------------------------------

#[test]
fn arrival_is_when_the_server_says_over_the_date_header() {
    let raw = b"Date: Thu, 01 Jan 2015 00:00:00 +0000\r\n\r\n";
    assert_eq!(aged_from_headers(1, raw, Some(NEW_YEAR_NOON)).ts, Some(NEW_YEAR_NOON));
}

#[test]
fn with_no_arrival_it_goes_by_the_date_and_with_neither_its_age_is_unknown() {
    let dated = b"From: a@notify-example.test\r\nDate: Thu, 01 Jan 2026 12:00:00 +0000\r\n\r\n";
    assert_eq!(aged_from_headers(1, dated, None).ts, Some(NEW_YEAR_NOON));
    assert_eq!(aged_from_headers(1, b"From: a@notify-example.test\r\n\r\n", None).ts, None);
    assert_eq!(aged_from_headers(1, b"Subject: no date\r\nDate: someday\r\n\r\n", None).ts, None);
}

#[test]
fn a_message_is_read_from_its_headers_and_when_it_arrived() {
    let raw = b"From: =?utf-8?b?QWRh?= <Ada@Notify-Example.TEST>\r\nSubject: =?utf-8?b?SGVsbG8=?=\r\n\
                Message-ID:  <m@notify-example.test> \r\n\r\n";
    assert_eq!(
        aged_from_headers(7, raw, Some(NEW_YEAR_NOON)),
        Aged {
            uid: "7".into(),
            from: "ada@notify-example.test".into(),
            subject: "Hello".into(),
            mid: "<m@notify-example.test>".into(),
            ts: Some(NEW_YEAR_NOON),
        }
    );
}

#[test]
fn expiry_asks_the_server_for_what_it_reads() {
    assert_eq!(
        AGE_FIELDS.iter().copied().collect::<BTreeSet<_>>(),
        BTreeSet::from(["DATE", "FROM", "MESSAGE-ID", "SUBJECT"])
    );
}

#[test]
fn the_audit_says_which_rule_took_it() {
    let target = Expired {
        aged: Aged {
            uid: "9".into(),
            from: "a@notify-example.test".into(),
            subject: "Old".into(),
            mid: "<m@notify-example.test>".into(),
            ts: Some(NOW),
        },
        hours: 72,
    };
    assert_eq!(
        audited(&[target]),
        [Audited {
            uid: "9".into(),
            from: "a@notify-example.test".into(),
            subject: "Old".into(),
            bucket: "expired".into(),
            reason: "expire rule: older than 72h".into(),
        }]
    );
}

// -- the fakes -----------------------------------------------------------------------------------------------------

/// An INBOX: each message's header fields and when it arrived, by UID.
type Inbox = BTreeMap<u32, (Vec<u8>, i64)>;

/// A message on the server: its UID, sender and when it arrived.
fn note(uid: u32, sender: &str, arrived: i64) -> (u32, (Vec<u8>, i64)) {
    let raw = format!("From: {sender}\r\nSubject: note {uid}\r\nMessage-ID: <n{uid}@example.test>\r\n\r\n");
    (uid, (raw.into_bytes(), arrived))
}

/// Two notes past the 72 hours, and one that came an hour ago.
fn mail() -> Inbox {
    BTreeMap::from([
        note(1, "bot@notify-example.test", OLD),
        note(2, "alerts@notify-example.test", OLD),
        note(3, "bot@notify-example.test", NOW - HOUR),
    ])
}

/// Every port expiry has, writing in one list what was asked of each, in order.
#[derive(Debug)]
struct World {
    rules: Vec<(String, i64)>,
    emails: Vec<&'static str>,
    /// Each account's INBOX.
    inboxes: Mutex<HashMap<String, Inbox>>,
    /// Searches the server refuses (by the text searched), and those it loses the connection on.
    refused: Vec<&'static str>,
    lost: Vec<&'static str>,
    /// Lose the connection at the first MOVE.
    lose_move: bool,
    /// UIDs a MOVE is answered OK for, which stay.
    stuck: BTreeSet<u32>,
    unreachable: Vec<&'static str>,
    held: Vec<&'static str>,
    audit_fails: bool,
    folders_failing: Vec<&'static str>,
    said: Mutex<Vec<String>>,
}

impl World {
    fn new() -> World {
        World {
            rules: rules(&[("notify-example.test", 72)]),
            emails: vec![ADA],
            inboxes: Mutex::new(HashMap::from([(ADA.to_owned(), mail()), (BEA.to_owned(), mail())])),
            refused: Vec::new(),
            lost: Vec::new(),
            lose_move: false,
            stuck: BTreeSet::new(),
            unreachable: Vec::new(),
            held: Vec::new(),
            audit_fails: false,
            folders_failing: Vec::new(),
            said: Mutex::new(Vec::new()),
        }
    }

    fn tell(&self, what: String) {
        self.said.lock().unwrap().push(what);
    }

    fn said(&self) -> Vec<String> {
        self.said.lock().unwrap().clone()
    }

    fn inbox<T>(&self, account: &str, read: impl FnOnce(&mut Inbox) -> T) -> T {
        read(self.inboxes.lock().unwrap().get_mut(account).unwrap())
    }

    /// What was said of these kinds, by each line's first word.
    fn asked(&self, kinds: &[&str]) -> Vec<String> {
        self.said().into_iter().filter(|s| kinds.iter().any(|k| s.split(' ').next() == Some(*k))).collect()
    }
}

#[derive(Debug, Clone)]
struct Shared(Arc<World>);

fn expiry(world: World) -> (Arc<World>, Expiry) {
    let world = Arc::new(world);
    let shared = Arc::new(Shared(world.clone()));
    let expiry = Expiry::new(ExpiryParts {
        rules: shared.clone(),
        boxes: shared.clone(),
        audit: shared.clone(),
        rows: shared.clone(),
        filing: shared.clone(),
        clock: shared,
    });
    (world, expiry)
}

fn account(email: &str) -> SweptAccount {
    SweptAccount { email: email.into(), kind: "imap".into(), trash: "Trash".into() }
}

/// A session on one account's server.
struct Session(Arc<World>, String);

impl LiveSession for Session {
    fn select(&mut self, folder: &str, readonly: bool) -> Result<(), LiveError> {
        self.0.tell(format!("{} {folder}", if readonly { "examine" } else { "select" }));
        Ok(())
    }
    fn uids(&mut self) -> Result<Vec<u32>, LiveError> {
        Ok(self.0.inbox(&self.1, |inbox| inbox.keys().copied().collect()))
    }
    /// The server's match is coarse: every message, which expiry narrows.
    fn uids_from(&mut self, text: &str) -> Result<Vec<u32>, LiveError> {
        self.0.tell(format!("search {text}"));
        if self.0.lost.contains(&text) {
            return Err(LiveError::Lost("abort"));
        }
        if self.0.refused.contains(&text) {
            return Err(LiveError::Refused("error"));
        }
        self.uids()
    }
    fn headers(&mut self, uids: &[u32], fields: &[&str]) -> Result<Vec<(u32, Vec<u8>)>, LiveError> {
        assert_eq!(fields, AGE_FIELDS);
        Ok(self.0.inbox(&self.1, |inbox| uids.iter().filter_map(|u| Some((*u, inbox.get(u)?.0.clone()))).collect()))
    }
    fn arrivals(&mut self, uids: &[u32]) -> Result<BTreeMap<u32, i64>, LiveError> {
        Ok(self.0.inbox(&self.1, |inbox| uids.iter().filter_map(|u| Some((*u, inbox.get(u)?.1))).collect()))
    }
    fn move_to(&mut self, uids: &[u32], dest: &str) -> Result<(), LiveError> {
        self.0.tell(format!("move {uids:?} {dest}"));
        if self.0.lose_move {
            return Err(LiveError::Lost("abort"));
        }
        self.0.inbox(&self.1, |inbox| {
            for uid in uids.iter().filter(|u| !self.0.stuck.contains(u)) {
                inbox.remove(uid);
            }
        });
        Ok(())
    }
    fn close(&mut self, failed: bool) {
        self.0.tell(if failed { "hangup".into() } else { "logout".into() });
    }
}

impl LiveInboxes for Shared {
    fn accounts(&self) -> Result<Vec<SweptAccount>, Fault> {
        self.0.tell("accounts".into());
        Ok(self.0.emails.iter().map(|e| account(e)).collect())
    }
    fn on_gate(&self, _: &str, job: &mut dyn FnMut() -> Result<Value, Fault>) -> Result<Value, Fault> {
        job()
    }
    fn on_gate_unless_held(
        &self,
        account: &str,
        job: &mut dyn FnMut() -> Result<Value, Fault>,
    ) -> Result<Option<Value>, Fault> {
        if self.0.held.contains(&account) {
            return Ok(None);
        }
        job().map(Some)
    }
    fn open(&self, account: &str) -> Result<Box<dyn LiveSession>, &'static str> {
        self.0.tell(format!("open {account}"));
        if self.0.unreachable.contains(&account) {
            return Err("OSError");
        }
        Ok(Box::new(Session(self.0.clone(), account.to_owned())))
    }
}

impl RuleBook for Shared {
    fn load(&self) -> Result<Rules, Fault> {
        Ok(Rules { expire_hours: self.0.rules.clone(), ..Rules::default() })
    }
    fn change(&self, _: &mut dyn FnMut(&mut Rules) -> Result<(), Failure>) -> Result<(), Failure> {
        Ok(())
    }
}

impl SweepTrail for Shared {
    fn swept(&self, account: &str, rows: &[Audited]) -> Result<(), Fault> {
        let rows: Vec<String> = rows.iter().map(|r| format!("{}:{}:{}", r.uid, r.bucket, r.reason)).collect();
        self.0.tell(format!("audit {account} {rows:?}"));
        if self.0.audit_fails {
            return Err(Fault::new("OperationalError"));
        }
        Ok(())
    }
    fn cleaned_up(&self, _: &str, _: &str, _: usize, _: &str) -> Result<(), Fault> {
        Ok(())
    }
}

impl SweptRows for Shared {
    fn forget_moved(&self, _: &str, _: &[(String, String)], _: &[String]) -> Result<(), Fault> {
        unreachable!("expiry forgets what it moved as expired")
    }
    fn forget_expired(&self, account: &str, moved: &[(String, String)], stragglers: &[String]) -> Result<(), Fault> {
        self.0.tell(format!("forget {account} {moved:?} {stragglers:?}"));
        Ok(())
    }
}

impl Filing for Shared {
    fn trash_now(&self, _: i64) -> Result<bool, Fault> {
        Ok(true)
    }
    fn trash(&self, _: i64, _: &str) -> Result<Value, Failure> {
        Ok(Value::Null)
    }
    fn trash_many(&self, _: &[NamedCopy], _: &str) -> Result<Value, Failure> {
        Ok(Value::Null)
    }
    fn undo_push(&self, _: &Value, _: &str) -> Result<(), Fault> {
        Ok(())
    }
    fn stats_changed(&self) {}
    fn folders_changed(&self, account: &str, moved: usize) -> Result<(), Fault> {
        self.0.tell(format!("folders_changed {account} {moved}"));
        if self.0.folders_failing.contains(&account) {
            return Err(Fault::new("OperationalError"));
        }
        Ok(())
    }
}

impl Clock for Shared {
    fn now(&self) -> Moment {
        Moment::from_micros(NOW * 1_000_000)
    }
}

const AUDITED: &str =
    r#"audit ada@example.test ["1:expired:expire rule: older than 72h", "2:expired:expire rule: older than 72h"]"#;
const FORGOTTEN: &str = r#"forget ada@example.test [("1", "<n1@example.test>"), ("2", "<n2@example.test>")] []"#;

// -- one account's pass ------------------------------------------------------------------------------------------

#[test]
fn mail_past_its_rule_is_audited_then_moved_then_forgotten() {
    let (world, expiry) = expiry(World::new());
    let out = expiry.account(&account(ADA), false).unwrap();
    assert_eq!(out, json!({"account": ADA, "planned": 2, "moved": 2, "stragglers": [], "error": null}));
    assert_eq!(
        world.said(),
        [
            "open ada@example.test",
            "examine INBOX",
            "search notify-example.test",
            AUDITED,
            "select INBOX",
            "move [1, 2] Trash",
            "examine INBOX",
            "logout",
            FORGOTTEN,
        ]
    );
    assert_eq!(world.inbox(ADA, |inbox| inbox.keys().copied().collect::<Vec<_>>()), [3]);
}

#[test]
fn what_wont_move_comes_back_as_a_straggler_and_isnt_forgotten_as_moved() {
    let (world, expiry) = expiry(World { stuck: BTreeSet::from([2]), ..World::new() });
    let out = expiry.account(&account(ADA), false).unwrap();
    assert_eq!((&out["moved"], &out["stragglers"]), (&json!(1), &json!(["2"])));
    assert_eq!(
        world.asked(&["forget"]),
        [r#"forget ada@example.test [("1", "<n1@example.test>"), ("2", "<n2@example.test>")] ["2"]"#]
    );
}

#[test]
fn a_dry_run_says_what_it_would_take_and_takes_nothing() {
    let (world, expiry) = expiry(World::new());
    let out = expiry.account(&account(ADA), true).unwrap();
    assert_eq!((&out["planned"], &out["moved"]), (&json!(2), &json!(0)));
    assert!(world.asked(&["audit", "move", "forget"]).is_empty());
    assert_eq!(world.asked(&["logout", "hangup"]), ["logout"]);
}

#[test]
fn with_no_rules_no_server_is_asked() {
    let (world, expiry) = expiry(World { rules: Vec::new(), ..World::new() });
    assert_eq!(expiry.account(&account(ADA), false).unwrap()["planned"], 0);
    assert!(world.said().is_empty());
}

#[test]
fn with_nothing_past_its_age_nothing_is_audited_or_moved() {
    let (world, expiry) = expiry(World { rules: rules(&[("notify-example.test", 200_000)]), ..World::new() });
    assert_eq!(
        expiry.account(&account(ADA), false).unwrap(),
        json!({"account": ADA, "planned": 0, "moved": 0, "stragglers": [], "error": null})
    );
    assert!(world.asked(&["audit", "move", "forget"]).is_empty());
}

#[test]
fn a_search_the_server_refuses_finds_nothing_and_the_other_rules_go_on() {
    let world = World {
        rules: rules(&[("notify-example.test", 72), ("news.example.test", 24)]),
        refused: vec!["news.example.test"],
        ..World::new()
    };
    let (world, expiry) = expiry(world);
    assert_eq!(expiry.account(&account(ADA), false).unwrap()["moved"], 2);
    assert_eq!(world.asked(&["search"]), ["search notify-example.test", "search news.example.test"]);
}

#[test]
fn a_connection_lost_is_an_error_said_by_its_class_and_nothing_moves() {
    let (world, expiry) = expiry(World { lost: vec!["notify-example.test"], ..World::new() });
    let out = expiry.account(&account(ADA), false).unwrap();
    assert_eq!(out, json!({"account": ADA, "planned": 0, "moved": 0, "stragglers": [], "error": "abort"}));
    assert!(world.asked(&["audit", "move", "forget"]).is_empty());
    // A connection that went is hung up, not logged out of.
    assert_eq!(world.asked(&["logout", "hangup"]), ["hangup"]);
}

#[test]
fn a_move_the_connection_loses_is_an_error_and_nothing_is_forgotten() {
    let (world, expiry) = expiry(World { lose_move: true, ..World::new() });
    let out = expiry.account(&account(ADA), false).unwrap();
    assert_eq!(out, json!({"account": ADA, "planned": 2, "moved": 0, "stragglers": [], "error": "abort"}));
    assert_eq!(world.asked(&["audit"]), [AUDITED]);
    assert!(world.asked(&["forget"]).is_empty());
    assert_eq!(world.asked(&["logout", "hangup"]), ["hangup"]);
}

#[test]
fn an_audit_that_cant_be_written_moves_nothing() {
    let (world, expiry) = expiry(World { audit_fails: true, ..World::new() });
    let out = expiry.account(&account(ADA), false).unwrap();
    assert_eq!((&out["planned"], &out["moved"], &out["error"]), (&json!(2), &json!(0), &json!("OperationalError")));
    assert!(world.asked(&["move", "forget"]).is_empty());
    assert_eq!(world.asked(&["logout", "hangup"]), ["logout"]);
}

#[test]
fn an_account_that_cant_be_reached_is_an_error_said_by_its_class() {
    let (_world, expiry) = expiry(World { unreachable: vec![ADA], ..World::new() });
    let out = expiry.account(&account(ADA), false).unwrap();
    assert_eq!((&out["error"], &out["planned"]), (&json!("OSError"), &json!(0)));
}

// -- one account's turn on its gate --------------------------------------------------------------------------------

#[test]
fn a_turn_that_moved_mail_says_the_folders_changed() {
    let (world, expiry) = expiry(World::new());
    expiry.auto_one(&account(ADA)).unwrap();
    assert_eq!(world.asked(&["folders_changed"]), ["folders_changed ada@example.test 2"]);
}

#[test]
fn a_turn_that_moved_nothing_says_nothing() {
    let world = World::new();
    world.inbox(ADA, |inbox| inbox.retain(|uid, _| *uid == 3));
    let (world, expiry) = expiry(world);
    expiry.auto_one(&account(ADA)).unwrap();
    assert!(world.asked(&["folders_changed"]).is_empty());
}

#[test]
fn a_turn_that_failed_changes_no_folder() {
    let (world, expiry) = expiry(World { unreachable: vec![ADA], ..World::new() });
    expiry.auto_one(&account(ADA)).unwrap();
    assert!(world.asked(&["folders_changed"]).is_empty());
}

#[test]
fn a_turn_gives_way_to_whoever_holds_the_accounts_gate() {
    let (world, expiry) = expiry(World { held: vec![ADA], ..World::new() });
    expiry.auto_one(&account(ADA)).unwrap();
    assert!(world.said().is_empty());
}

// -- the background round ----------------------------------------------------------------------------------------

#[test]
fn a_round_with_no_rules_reaches_no_account() {
    let (world, expiry) = expiry(World { rules: Vec::new(), emails: vec![ADA, BEA], ..World::new() });
    expiry.round().unwrap();
    assert!(world.said().is_empty());
}

#[test]
fn a_round_gives_each_account_its_turn_and_one_failing_stops_none() {
    let (world, expiry) = expiry(World { emails: vec![ADA, BEA], folders_failing: vec![ADA], ..World::new() });
    expiry.round().unwrap();
    assert_eq!(
        world.asked(&["folders_changed"]),
        ["folders_changed ada@example.test 2", "folders_changed bea@example.test 2"]
    );
    assert_eq!(world.inbox(BEA, |inbox| inbox.len()), 1);
}

#[test]
fn a_round_goes_to_each_account_and_moves_only_its_own_mail() {
    let (world, expiry) = expiry(World { emails: vec![ADA, BEA], ..World::new() });
    world.inbox(BEA, |inbox| inbox.extend([note(4, "bot@notify-example.test", OLD)]));
    expiry.round().unwrap();
    assert_eq!(world.asked(&["accounts", "open"]), ["accounts", "open ada@example.test", "open bea@example.test"]);
    assert_eq!(
        world.asked(&["folders_changed"]),
        ["folders_changed ada@example.test 2", "folders_changed bea@example.test 3"]
    );
}
