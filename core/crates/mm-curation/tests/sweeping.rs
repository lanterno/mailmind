//! A sweep, held to the Python app's answers (tests/unit/mailbox/test_bulk_moves.py, and what
//! tests/api/test_http_settings.py's sweep shows): the moves checked and tried once more, then the sweep of an account
//! on fakes behind Curation's ports.

use std::collections::{BTreeSet, HashMap};
use std::sync::{Arc, Mutex};

use mm_curation::cleanup_ports::{
    Audited, LiveError, LiveInboxes, LiveSession, NamedCopy, SweepTrail, SweptAccount, SweptRows,
};
use mm_curation::ports::{Filing, RuleBook};
use mm_curation::rulebook::Rules;
use mm_curation::sweeping::{MOVE_BATCH, READ_ONLY, Sweeps, SweepsParts, move_verified, read_from_headers};
use mm_kernel::errors::{Failure, Fault, Kind};
use mm_kernel::seams::{Clock, Moment};
use serde_json::{Value, json};

/// A server's INBOX. A MOVE naming a `refused` UID is a NO; an `ignored` one is answered OK and kept for that many
/// MOVEs; `lost` ends the connection at the first MOVE of that many UIDs.
#[derive(Debug, Default)]
struct Inbox {
    uids_in: BTreeSet<u32>,
    refused: BTreeSet<u32>,
    ignored: HashMap<u32, u32>,
    lost: Option<usize>,
    heads: HashMap<u32, Vec<u8>>,
    moves: Vec<(Vec<u32>, String)>,
    selected: Vec<(String, bool)>,
    closed: Option<bool>,
}

impl Inbox {
    fn of(uids: impl IntoIterator<Item = u32>) -> Inbox {
        Inbox { uids_in: uids.into_iter().collect(), ..Inbox::default() }
    }
}

impl LiveSession for Inbox {
    fn select(&mut self, folder: &str, readonly: bool) -> Result<(), LiveError> {
        self.selected.push((folder.into(), readonly));
        Ok(())
    }

    fn uids(&mut self) -> Result<Vec<u32>, LiveError> {
        Ok(self.uids_in.iter().copied().collect())
    }

    fn headers(&mut self, uids: &[u32], _: &[&str]) -> Result<Vec<(u32, Vec<u8>)>, LiveError> {
        Ok(uids.iter().map(|u| (*u, self.heads.get(u).cloned().unwrap_or_default())).collect())
    }

    fn move_to(&mut self, uids: &[u32], dest: &str) -> Result<(), LiveError> {
        if self.lost == Some(uids.len()) {
            return Err(LiveError::Lost("ConnectionLost"));
        }
        self.moves.push((uids.to_vec(), dest.into()));
        if uids.iter().any(|u| self.refused.contains(u)) {
            return Err(LiveError::Refused("Refused"));
        }
        for u in uids {
            match self.ignored.get_mut(u) {
                Some(n) if *n > 0 => *n -= 1,
                _ => {
                    self.uids_in.remove(u);
                }
            }
        }
        Ok(())
    }

    fn close(&mut self, failed: bool) {
        self.closed = Some(failed);
    }
}

fn texts(range: std::ops::RangeInclusive<u32>) -> Vec<String> {
    range.map(|u| u.to_string()).collect()
}

fn trash(range: std::ops::RangeInclusive<u32>) -> (Vec<u32>, String) {
    (range.collect(), "Trash".into())
}

#[test]
fn all_go_a_batch_at_a_time_and_none_is_left() {
    let last = u32::try_from(MOVE_BATCH).expect("small") + 1;
    let mut inbox = Inbox::of(1..=last);
    assert_eq!(move_verified(&mut inbox, &texts(1..=last), "Trash"), Ok((MOVE_BATCH + 1, vec![])));
    assert_eq!(inbox.moves, [trash(1..=last - 1), trash(last..=last)]);
    assert_eq!(inbox.selected[0], ("INBOX".into(), false));
}

#[test]
fn a_refused_batch_goes_one_at_a_time_and_one_bad_uid_sinks_no_other() {
    let mut inbox = Inbox { refused: [2].into(), ..Inbox::of(1..=3) };
    assert_eq!(move_verified(&mut inbox, &texts(1..=3), "Trash"), Ok((2, vec!["2".into()])));
    assert_eq!(inbox.moves, [trash(1..=3), trash(1..=1), trash(2..=2), trash(3..=3), trash(2..=2)]);
    assert_eq!(inbox.uids_in, [2].into());
}

#[test]
fn one_still_there_after_the_pass_is_tried_once_more() {
    let mut inbox = Inbox { ignored: [(2, 1)].into(), ..Inbox::of(1..=3) };
    assert_eq!(move_verified(&mut inbox, &texts(1..=3), "Trash"), Ok((3, vec![])));
    assert_eq!(inbox.moves, [trash(1..=3), trash(2..=2)]);
    let inbox_as = |readonly| ("INBOX".to_owned(), readonly);
    assert_eq!(inbox.selected, [inbox_as(false), inbox_as(true), inbox_as(false), inbox_as(true)]);
}

#[test]
fn one_still_there_after_its_second_try_is_a_straggler() {
    let mut inbox = Inbox { ignored: [(3, 2)].into(), ..Inbox::of(1..=3) };
    assert_eq!(move_verified(&mut inbox, &texts(1..=3), "Trash"), Ok((2, vec!["3".into()])));
}

#[test]
fn mail_it_wasnt_asked_to_move_stays_out_of_it() {
    let mut inbox = Inbox { ignored: [(2, 1)].into(), ..Inbox::of(1..=5) };
    assert_eq!(move_verified(&mut inbox, &texts(1..=2), "Trash"), Ok((2, vec![])));
    assert_eq!(inbox.uids_in, [3, 4, 5].into());
    assert_eq!(inbox.moves.last(), Some(&trash(2..=2)));
}

#[test]
fn a_lost_connection_stops_it_all_even_one_at_a_time() {
    let mut gone = Inbox { lost: Some(2), ..Inbox::of(1..=2) };
    assert_eq!(move_verified(&mut gone, &texts(1..=2), "Trash"), Err(LiveError::Lost("ConnectionLost")));
    let mut midway = Inbox { lost: Some(1), refused: [1].into(), ..Inbox::of(1..=2) };
    assert_eq!(move_verified(&mut midway, &texts(1..=2), "Trash"), Err(LiveError::Lost("ConnectionLost")));
}

#[test]
fn a_message_is_read_from_the_fields_a_sweep_asks_for() {
    let read = read_from_headers(
        7,
        b"From: The Weekly Loom <News@Loom.example.test>\r\nSubject: Issue 218\r\n\
          Message-ID: <issue-218@loom.example.test>\r\nList-Id: <loom.example.test>\r\n\r\n",
    );
    assert_eq!(
        (read.uid.as_str(), read.from.as_str(), read.name.as_str()),
        ("7", "news@loom.example.test", "The Weekly Loom")
    );
    assert_eq!(
        (read.subject.as_str(), read.mid.as_str(), read.bulk),
        ("Issue 218", "<issue-218@loom.example.test>", true)
    );
}

// ── a sweep of an account ────────────────────────────────────────────────

/// The accounts, their inboxes and everything the sweep writes, each noted.
#[derive(Debug, Default)]
struct World {
    inbox: Mutex<Option<Inbox>>,
    refuse_open: bool,
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

/// The world as a `'static` session needs it: what it noted lives in the world.
#[derive(Debug, Clone)]
struct Shared(Arc<World>);

/// A session on the world's inbox, which it hands back as it closes.
struct Session(Shared, Option<Inbox>);

impl Session {
    fn inbox(&mut self) -> &mut Inbox {
        self.1.as_mut().expect("open")
    }
}

impl LiveSession for Session {
    fn select(&mut self, folder: &str, readonly: bool) -> Result<(), LiveError> {
        self.inbox().select(folder, readonly)
    }
    fn uids(&mut self) -> Result<Vec<u32>, LiveError> {
        self.inbox().uids()
    }
    fn headers(&mut self, uids: &[u32], fields: &[&str]) -> Result<Vec<(u32, Vec<u8>)>, LiveError> {
        self.inbox().headers(uids, fields)
    }
    fn move_to(&mut self, uids: &[u32], dest: &str) -> Result<(), LiveError> {
        self.inbox().move_to(uids, dest)
    }
    fn close(&mut self, failed: bool) {
        let mut inbox = self.1.take().expect("open");
        inbox.close(failed);
        *self.0.0.inbox.lock().expect("lock") = Some(inbox);
    }
}

impl LiveInboxes for Shared {
    fn accounts(&self) -> Result<Vec<SweptAccount>, Fault> {
        Ok(vec![SweptAccount { email: "ada@example.test".into(), kind: "imap".into(), trash: "Trash".into() }])
    }
    fn on_gate(&self, account: &str, job: &mut dyn FnMut() -> Result<Value, Fault>) -> Result<Value, Fault> {
        self.0.note(format!("gate {account}"));
        job()
    }
    fn open(&self, _: &str) -> Result<Box<dyn LiveSession>, &'static str> {
        if self.0.refuse_open {
            return Err("ConnectionRefused");
        }
        let inbox = self.0.inbox.lock().expect("lock").take().expect("one session at a time");
        Ok(Box::new(Session(self.clone(), Some(inbox))))
    }
}

impl RuleBook for Shared {
    fn load(&self) -> Result<Rules, Fault> {
        Ok(Rules::default())
    }
    fn change(&self, _: &mut dyn FnMut(&mut Rules) -> Result<(), Failure>) -> Result<(), Failure> {
        Ok(())
    }
}

impl SweepTrail for Shared {
    fn swept(&self, account: &str, rows: &[Audited]) -> Result<(), Fault> {
        for r in rows {
            self.0.note(format!("audit {account} {} {} {}", r.uid, r.bucket, r.reason));
        }
        Ok(())
    }
    fn cleaned_up(&self, _: &str, _: &str, _: usize, _: &str) -> Result<(), Fault> {
        Ok(())
    }
}

impl SweptRows for Shared {
    fn forget_moved(&self, account: &str, moved: &[(String, String)], stragglers: &[String]) -> Result<(), Fault> {
        self.0.note(format!("forget {account} {moved:?} {stragglers:?}"));
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
    fn stats_changed(&self) {
        self.0.note("stats".into());
    }
}

impl Clock for Shared {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000)
    }
}

fn heads(from: &str, subject: &str, listed: bool) -> Vec<u8> {
    let list = if listed { "List-Unsubscribe: <https://list.example.test/u>\r\n" } else { "" };
    format!("From: {from}\r\nSubject: {subject}\r\nMessage-ID: <{subject}@example.test>\r\n{list}\r\n").into_bytes()
}

fn world() -> Shared {
    let mut inbox = Inbox::of(1..=3);
    inbox.heads.insert(1, heads("lin@example.test", "lunch", false));
    inbox.heads.insert(2, heads("news@loom.example.test", "issue", true));
    inbox.heads.insert(3, heads("no-reply@alerts.example.test", "alert", false));
    Shared(Arc::new(World { inbox: Mutex::new(Some(inbox)), ..World::default() }))
}

fn sweeps(world: &Shared, writes_allowed: bool) -> Sweeps {
    Sweeps::new(SweepsParts {
        rules: Arc::new(world.clone()),
        boxes: Arc::new(world.clone()),
        audit: Arc::new(world.clone()),
        rows: Arc::new(world.clone()),
        filing: Arc::new(world.clone()),
        clock: Arc::new(world.clone()),
        writes_allowed,
    })
}

#[test]
fn a_dry_run_plans_and_moves_nothing() {
    let world = world();
    let done = sweeps(&world, true).run(Some("all"), true).expect("swept");
    assert_eq!(
        done,
        json!({"ok": true, "dry_run": true, "generated": "2026-11-02T12:00:00+00:00", "results": [{
            "account": "ada@example.test", "planned": 2,
            "by_bucket": {"other/personal-ish": 1, "newsletters/marketing": 1, "automated-notifications": 1},
            "senders": 2, "moved": 0, "stragglers": [], "error": null}]})
    );
    let inbox = world.0.inbox.lock().expect("lock").take().expect("closed");
    assert_eq!((inbox.moves.len(), inbox.closed), (0, Some(false)));
    assert_eq!(world.0.said(), ["gate ada@example.test"]);
}

#[test]
fn a_sweep_records_what_it_will_trash_moves_it_and_forgets_its_rows() {
    let world = world();
    let done = sweeps(&world, true).run(None, false).expect("swept");
    assert_eq!((done["results"][0]["moved"].clone(), done["results"][0]["planned"].clone()), (json!(2), json!(2)));
    let inbox = world.0.inbox.lock().expect("lock").take().expect("closed");
    assert_eq!(inbox.moves, [(vec![2, 3], "Trash".to_owned())]);
    assert_eq!(
        world.0.said(),
        [
            "gate ada@example.test",
            "audit ada@example.test 2 newsletters/marketing List-Unsubscribe header",
            "audit ada@example.test 3 automated-notifications no-reply / notification address",
            "forget ada@example.test [(\"2\", \"<issue@example.test>\"), (\"3\", \"<alert@example.test>\")] []",
            "stats",
        ]
    );
}

#[test]
fn a_read_only_process_only_plans() {
    let world = world();
    assert_eq!(sweeps(&world, false).run(None, false), Err(Failure::Fault(READ_ONLY)));
    assert!(sweeps(&world, false).run(None, true).is_ok());
}

#[test]
fn an_account_that_isnt_one_is_refused() {
    let refused = sweeps(&world(), true).run(Some("bea@example.test"), true);
    assert!(matches!(refused, Err(Failure::Problem(p)) if p.kind == Kind::Refused && p.said == "unknown account"));
}

#[test]
fn a_server_that_cant_be_reached_says_its_class_alone() {
    let world = Shared(Arc::new(World { refuse_open: true, ..World::default() }));
    let done = sweeps(&world, true).run(Some("ada@example.test"), false).expect("swept");
    assert_eq!(done["results"], json!([{"account": "ada@example.test", "error": "ConnectionRefused", "planned": 0}]));
}
