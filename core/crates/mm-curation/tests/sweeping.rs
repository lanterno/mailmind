//! A sweep, held to the Python app's answers (tests/unit/mailbox/test_bulk_moves.py, and what
//! tests/api/test_http_settings.py's sweep shows): the moves checked and tried once more, then the sweep of an account
//! on fakes behind Curation's ports.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::sync::{Arc, Mutex};

use mm_curation::cleanup_ports::{
    Audited, Ids, LiveError, LiveInboxes, LiveSession, NamedCopy, SweepTrail, SweptAccount, SweptRows,
};
use mm_curation::ports::{Filing, RuleBook};
use mm_curation::rulebook::Rules;
use mm_curation::sweeping::{MOVE_BATCH, READ_ONLY, SWEEP_MAX, Sweeps, SweepsParts, move_verified, read_from_headers};
use mm_kernel::errors::{Failure, Fault, Kind};
use mm_kernel::seams::{Clock, Moment};
use serde_json::{Value, json};

/// A server's INBOX. A MOVE naming a `refused` UID is a NO; an `ignored` one is answered OK and kept for that many
/// MOVEs; `lost` ends the connection at the first MOVE of that many UIDs, and `lost_after` once that MOVE is done.
#[derive(Debug, Default)]
struct Inbox {
    uids_in: BTreeSet<u32>,
    refused: BTreeSet<u32>,
    ignored: HashMap<u32, u32>,
    lost: Option<usize>,
    lost_after: Option<usize>,
    heads: HashMap<u32, Vec<u8>>,
    flags: HashMap<u32, Vec<String>>,
    moves: Vec<(Vec<u32>, String)>,
    selected: Vec<(String, bool)>,
    closed: Option<bool>,
    /// The folder playing Trash on the server, where the account's own isn't listed.
    trash_listed: Option<String>,
    /// The connection ends as the server's folders are listed.
    lost_listing: bool,
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

    fn uids_from(&mut self, _: &str) -> Result<Vec<u32>, LiveError> {
        unreachable!("a sweep reads the whole INBOX")
    }

    fn headers(&mut self, uids: &[u32], _: &[&str]) -> Result<Vec<(u32, Vec<u8>)>, LiveError> {
        Ok(uids.iter().map(|u| (*u, self.heads.get(u).cloned().unwrap_or_default())).collect())
    }

    fn flags(&mut self, uids: &[u32]) -> Result<BTreeMap<u32, Vec<String>>, LiveError> {
        Ok(uids.iter().filter_map(|u| Some((*u, self.flags.get(u)?.clone()))).collect())
    }

    fn arrivals(&mut self, _: &[u32]) -> Result<BTreeMap<u32, i64>, LiveError> {
        unreachable!("a sweep doesn't ask when mail arrived")
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
        if self.lost_after == Some(uids.len()) {
            self.lost_after = None;
            return Err(LiveError::Lost("ConnectionLost"));
        }
        Ok(())
    }

    fn trash(&mut self, named: &str) -> Result<String, LiveError> {
        if self.lost_listing {
            return Err(LiveError::Lost("ConnectionLost"));
        }
        Ok(self.trash_listed.clone().unwrap_or_else(|| named.to_owned()))
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
        Some(&["\\Seen".to_owned()][..]),
    );
    assert_eq!(
        (read.uid.as_str(), read.from.as_str(), read.name.as_str()),
        ("7", "news@loom.example.test", "The Weekly Loom")
    );
    assert_eq!(
        (read.subject.as_str(), read.mid.as_str(), read.bulk),
        ("Issue 218", "<issue-218@loom.example.test>", true)
    );
    assert_eq!((read.starred, read.unread, read.held()), (false, false, false));
}

#[test]
fn a_star_or_no_seen_flag_holds_a_message_back_whatever_the_case() {
    let flagged = |flags: &[&str]| {
        let flags: Vec<String> = flags.iter().map(|f| (*f).to_owned()).collect();
        let read = read_from_headers(1, b"From: a@example.test\r\n\r\n", Some(flags.as_slice()));
        (read.starred, read.unread, read.held())
    };
    assert_eq!(flagged(&["\\Seen"]), (false, false, false));
    assert_eq!(flagged(&["\\seen", "\\Answered"]), (false, false, false));
    assert_eq!(flagged(&["\\Seen", "\\FLAGGED"]), (true, false, true));
    assert_eq!(flagged(&["\\Flagged"]), (true, true, true));
    assert_eq!(flagged(&[]), (false, true, true));
    // A message whose flags the server didn't say counts as unread.
    assert!(read_from_headers(1, b"From: a@example.test\r\n\r\n", None).held());
}

// ── a sweep of an account ────────────────────────────────────────────────

/// The accounts, their inboxes and everything the sweep writes, each noted. `opens` sessions are opened, and none
/// after them.
#[derive(Debug, Default)]
struct World {
    inbox: Mutex<Option<Inbox>>,
    refuse_open: bool,
    opens: Option<usize>,
    opened: Mutex<usize>,
    said: Mutex<Vec<String>>,
    /// Each way back put on the stack, as it was put.
    pushed: Mutex<Vec<Value>>,
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
    fn uids_from(&mut self, text: &str) -> Result<Vec<u32>, LiveError> {
        self.inbox().uids_from(text)
    }
    fn headers(&mut self, uids: &[u32], fields: &[&str]) -> Result<Vec<(u32, Vec<u8>)>, LiveError> {
        self.inbox().headers(uids, fields)
    }
    fn flags(&mut self, uids: &[u32]) -> Result<BTreeMap<u32, Vec<String>>, LiveError> {
        self.inbox().flags(uids)
    }
    fn arrivals(&mut self, uids: &[u32]) -> Result<BTreeMap<u32, i64>, LiveError> {
        self.inbox().arrivals(uids)
    }
    fn move_to(&mut self, uids: &[u32], dest: &str) -> Result<(), LiveError> {
        self.inbox().move_to(uids, dest)
    }
    fn trash(&mut self, named: &str) -> Result<String, LiveError> {
        self.inbox().trash(named)
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
    fn on_gate_unless_held(
        &self,
        _: &str,
        _: &mut dyn FnMut() -> Result<Value, Fault>,
    ) -> Result<Option<Value>, Fault> {
        unreachable!("a sweep a person asked for waits its turn")
    }
    fn open(&self, _: &str) -> Result<Box<dyn LiveSession>, &'static str> {
        let opened = {
            let mut opened = self.0.opened.lock().expect("lock");
            *opened += 1;
            *opened
        };
        if self.0.refuse_open || self.0.opens.is_some_and(|opens| opened > opens) {
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
    fn forget_expired(&self, _: &str, _: &[(String, String)], _: &[String]) -> Result<(), Fault> {
        unreachable!("a sweep forgets what it moved as moved")
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
        unreachable!("a sweep puts its ways back on the stack at one write")
    }
    fn undo_push_many(&self, entries: &[Value], group: &str) -> Result<(), Fault> {
        let ids: Vec<&str> = entries.iter().map(|e| e["message_id"].as_str().unwrap_or_default()).collect();
        self.0.note(format!("undo {group} {ids:?}"));
        self.0.pushed.lock().expect("lock").extend_from_slice(entries);
        Ok(())
    }
    fn stats_changed(&self) {
        self.0.note("stats".into());
    }
    fn folders_changed(&self, _: &str, _: usize) -> Result<(), Fault> {
        unreachable!("a sweep says the counts moved")
    }
}

impl Ids for Shared {
    fn hex(&self, n: usize) -> String {
        "a".repeat(2 * n)
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

/// Lin's lunch, Loom's issue and an alert, each read.
fn world() -> Shared {
    let mut inbox = Inbox::of(1..=3);
    inbox.heads.insert(1, heads("lin@example.test", "lunch", false));
    inbox.heads.insert(2, heads("news@loom.example.test", "issue", true));
    inbox.heads.insert(3, heads("no-reply@alerts.example.test", "alert", false));
    for uid in 1..=3 {
        inbox.flags.insert(uid, vec!["\\Seen".into()]);
    }
    Shared(Arc::new(World { inbox: Mutex::new(Some(inbox)), ..World::default() }))
}

/// More messages in the world's inbox: (uid, its fields, its flags).
fn more(world: &Shared, mail: &[(u32, Vec<u8>, &[&str])]) {
    let mut inbox = world.0.inbox.lock().expect("lock");
    let inbox = inbox.as_mut().expect("closed");
    for (uid, heads, flags) in mail {
        inbox.uids_in.insert(*uid);
        inbox.heads.insert(*uid, heads.clone());
        inbox.flags.insert(*uid, flags.iter().map(|f| (*f).to_owned()).collect());
    }
}

fn sweeps(world: &Shared, writes_allowed: bool) -> Sweeps {
    Sweeps::new(SweepsParts {
        rules: Arc::new(world.clone()),
        boxes: Arc::new(world.clone()),
        audit: Arc::new(world.clone()),
        rows: Arc::new(world.clone()),
        filing: Arc::new(world.clone()),
        ids: Arc::new(world.clone()),
        clock: Arc::new(world.clone()),
        writes_allowed,
    })
}

#[test]
fn a_dry_run_plans_and_moves_nothing() {
    let world = world();
    let done = sweeps(&world, true).run(Some("all"), true, None).expect("swept");
    assert_eq!(
        done,
        json!({"ok": true, "dry_run": true, "generated": "2026-11-02T12:00:00+00:00", "undo_group": null, "results": [{
            "account": "ada@example.test", "planned": 2,
            "by_bucket": {"other/personal-ish": 1, "newsletters/marketing": 1, "automated-notifications": 1},
            "senders": 2, "held_back": 0, "capped": false, "no_undo": 0, "moved": 0, "stragglers": [],
            "error": null, "going": [
                {"bucket": "newsletters/marketing", "count": 1, "subjects": ["issue"],
                 "senders": [{"from_addr": "news@loom.example.test", "count": 1}]},
                {"bucket": "automated-notifications", "count": 1, "subjects": ["alert"],
                 "senders": [{"from_addr": "no-reply@alerts.example.test", "count": 1}]}]}]})
    );
    let inbox = world.0.inbox.lock().expect("lock").take().expect("closed");
    assert_eq!((inbox.moves.len(), inbox.closed), (0, Some(false)));
    assert_eq!(world.0.said(), ["gate ada@example.test"]);
}

#[test]
fn a_sweep_records_what_it_will_trash_moves_it_and_forgets_its_rows() {
    let world = world();
    let done = sweeps(&world, true).run(None, false, None).expect("swept");
    assert_eq!((done["results"][0]["moved"].clone(), done["results"][0]["planned"].clone()), (json!(2), json!(2)));
    let inbox = world.0.inbox.lock().expect("lock").take().expect("closed");
    assert_eq!(inbox.moves, [(vec![2, 3], "Trash".to_owned())]);
    assert_eq!(
        world.0.said(),
        [
            "gate ada@example.test",
            "audit ada@example.test 2 newsletters/marketing List-Unsubscribe header",
            "audit ada@example.test 3 automated-notifications no-reply / notification address",
            "undo sweep:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa [\"<issue@example.test>\", \"<alert@example.test>\"]",
            "forget ada@example.test [(\"2\", \"<issue@example.test>\"), (\"3\", \"<alert@example.test>\")] []",
            "stats",
        ]
    );
    assert_eq!(done["undo_group"], json!("sweep:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"));
}

#[test]
fn each_message_that_went_has_its_way_back_under_the_group_the_request_named() {
    let world = world();
    let done = sweeps(&world, true).run(None, false, Some("ug-1")).expect("swept");
    assert_eq!(done["undo_group"], json!("ug-1"));
    let undo: Vec<String> = world.0.said().into_iter().filter(|s| s.starts_with("undo")).collect();
    assert_eq!(undo, ["undo ug-1 [\"<issue@example.test>\", \"<alert@example.test>\"]"]);
    // A group a request leaves empty is none.
    let world = self::world();
    let done = sweeps(&world, true).run(None, false, Some("")).expect("swept");
    assert_eq!(done["undo_group"], json!("sweep:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"));
}

#[test]
fn a_way_back_is_a_sweep_back_from_trash_to_the_inbox_that_an_accounts_undo_takes_together() {
    let world = world();
    sweeps(&world, true).run(None, false, Some("ug-1")).expect("swept");
    let pushed = world.0.pushed.lock().expect("lock").clone();
    assert_eq!(
        pushed[0],
        json!({"kind": "sweep_back", "account": "ada@example.test", "src_folder": "INBOX", "dest_folder": "Trash",
               "message_id": "<issue@example.test>", "label": "Swept junk", "subject": "issue",
               "who": "news@loom.example.test"})
    );
    assert!(pushed.iter().all(|e| e["kind"] == "sweep_back"));
}

#[test]
fn a_straggler_or_a_message_with_no_message_id_has_no_way_back() {
    let world = world();
    more(
        &world,
        &[(4, b"From: deals@loom.example.test\r\nSubject: sale\r\nList-Id: <loom>\r\n\r\n".to_vec(), &["\\Seen"])],
    );
    world.0.inbox.lock().expect("lock").as_mut().expect("closed").ignored.insert(3, 2);
    let plan = sweeps(&world, true).run(None, true, None).expect("planned");
    assert_eq!((plan["results"][0]["planned"].clone(), plan["results"][0]["no_undo"].clone()), (json!(3), json!(1)));
    let done = sweeps(&world, true).run(None, false, None).expect("swept");
    assert_eq!(
        (
            done["results"][0]["moved"].clone(),
            done["results"][0]["stragglers"].clone(),
            done["results"][0]["no_undo"].clone()
        ),
        (json!(2), json!(["3"]), json!(1))
    );
    let undo: Vec<String> = world.0.said().into_iter().filter(|s| s.starts_with("undo")).collect();
    assert_eq!(undo, ["undo sweep:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa [\"<issue@example.test>\"]"]);
}

#[test]
fn nothing_that_went_leaves_the_stack_alone_and_the_answer_names_no_group() {
    let world = world();
    for uid in 1..=3 {
        world.0.inbox.lock().expect("lock").as_mut().expect("closed").flags.insert(uid, vec![]);
    }
    let done = sweeps(&world, true).run(None, false, None).expect("swept");
    assert_eq!((done["results"][0]["moved"].clone(), done["undo_group"].clone()), (json!(0), Value::Null));
    assert!(!world.0.said().iter().any(|s| s.starts_with("undo")));
}

/// What a sweep answered and noted of `world`'s account: (error, moved, undo group), and its undo and forget notes.
fn stopped(world: &Shared) -> ((Value, Value, Value), Vec<String>) {
    let done = sweeps(world, true).run(None, false, None).expect("swept");
    let said = (done["results"][0]["error"].clone(), done["results"][0]["moved"].clone(), done["undo_group"].clone());
    let notes = world.0.said().into_iter().filter(|s| s.starts_with("undo") || s.starts_with("forget")).collect();
    (said, notes)
}

#[test]
fn a_connection_lost_before_anything_moved_leaves_the_stack_as_it_was() {
    let world = world();
    world.0.inbox.lock().expect("lock").as_mut().expect("closed").lost = Some(2);
    let (said, notes) = stopped(&world);
    assert_eq!(said, (json!("ConnectionLost"), json!(0), Value::Null));
    assert_eq!(notes, Vec::<String>::new());
    let inbox = world.0.inbox.lock().expect("lock").take().expect("closed");
    assert_eq!((inbox.uids_in, inbox.closed), ([1, 2, 3].into(), Some(false)), "a fresh look found it all there");
}

#[test]
fn a_connection_lost_after_its_move_gives_a_way_back_to_what_a_fresh_look_finds_gone() {
    let world = world();
    world.0.inbox.lock().expect("lock").as_mut().expect("closed").lost_after = Some(2);
    let (said, notes) = stopped(&world);
    assert_eq!(said, (json!("ConnectionLost"), json!(2), json!("sweep:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")));
    assert_eq!(
        notes,
        [
            "forget ada@example.test [(\"2\", \"<issue@example.test>\"), (\"3\", \"<alert@example.test>\")] []",
            "undo sweep:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa [\"<issue@example.test>\", \"<alert@example.test>\"]",
        ]
    );
}

#[test]
fn a_server_that_cant_be_asked_again_leaves_a_way_back_for_all_it_meant_to_move() {
    let mut inbox = world().0.inbox.lock().expect("lock").take().expect("closed");
    inbox.lost = Some(2);
    let world = Shared(Arc::new(World { inbox: Mutex::new(Some(inbox)), opens: Some(1), ..World::default() }));
    let (said, notes) = stopped(&world);
    assert_eq!(said, (json!("ConnectionLost"), json!(0), json!("sweep:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")));
    assert_eq!(
        notes,
        ["undo sweep:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa [\"<issue@example.test>\", \"<alert@example.test>\"]"]
    );
}

#[test]
fn starred_and_unread_junk_stays_where_it_is() {
    let world = world();
    more(
        &world,
        &[
            (2, heads("news@loom.example.test", "issue", true), &["\\Seen", "\\Flagged"]),
            (3, heads("no-reply@alerts.example.test", "alert", false), &[]),
            (4, heads("deals@loom.example.test", "sale", true), &["\\Flagged"]),
            (5, heads("no-reply@alerts.example.test", "sign-in", false), &["\\Seen"]),
        ],
    );
    let plan = sweeps(&world, true).run(None, true, None).expect("planned");
    let said = |done: &Value, field: &str| done["results"][0][field].clone();
    assert_eq!((said(&plan, "planned"), said(&plan, "held_back")), (json!(1), json!(3)));
    let done = sweeps(&world, true).run(None, false, None).expect("swept");
    assert_eq!((said(&done, "moved"), said(&done, "held_back")), (json!(1), json!(3)));
    let inbox = world.0.inbox.lock().expect("lock").take().expect("closed");
    assert_eq!(inbox.moves, [(vec![5], "Trash".to_owned())]);
    assert_eq!(inbox.uids_in, [1, 2, 3, 4].into());
    let audited: Vec<String> = world.0.said().into_iter().filter(|s| s.starts_with("audit")).collect();
    assert_eq!(audited, ["audit ada@example.test 5 automated-notifications no-reply / notification address"]);
}

#[test]
fn a_sweep_moves_the_newest_of_its_junk_up_to_its_cap_and_says_more_is_left() {
    const SEEN: &[&str] = &["\\Seen"];
    let world = world();
    let last = 10 + SWEEP_MAX as u32;
    let mail: Vec<(u32, Vec<u8>, &[&str])> =
        (10..=last).map(|uid| (uid, heads("news@loom.example.test", &format!("issue-{uid}"), true), SEEN)).collect();
    more(&world, &mail);
    let said = |done: &Value, field: &str| done["results"][0][field].clone();
    let plan = sweeps(&world, true).run(None, true, None).expect("planned");
    assert_eq!((said(&plan, "planned"), said(&plan, "capped")), (json!(SWEEP_MAX), json!(true)));
    let done = sweeps(&world, true).run(None, false, None).expect("swept");
    assert_eq!((said(&done, "moved"), said(&done, "capped")), (json!(SWEEP_MAX), json!(true)));
    let inbox = world.0.inbox.lock().expect("lock").take().expect("closed");
    assert_eq!(inbox.uids_in, [1, 2, 3, 10].into(), "the oldest wait for the next sweep");
    let moved: Vec<u32> = inbox.moves.iter().flat_map(|(uids, _)| uids.iter().copied()).collect();
    assert_eq!(moved, (11..=last).collect::<Vec<u32>>(), "in the order the INBOX holds them");
}

#[test]
fn a_plan_names_the_senders_with_the_most_and_the_newest_subjects_of_each_bucket_that_goes() {
    const SEEN: &[&str] = &["\\Seen"];
    let world = world();
    let mail: Vec<(u32, Vec<u8>, &[&str])> = [
        (10, "a", "spring"),
        (11, "c", "spring"),
        (12, "b", "sale"),
        (13, "d", "summer"),
        (14, "e", "autumn"),
        (15, "b", "weekly"),
        (16, "f", "weekly"),
        (17, "news", "issue 2"),
    ]
    .into_iter()
    .map(|(uid, who, subject)| (uid, heads(&format!("{who}@loom.example.test"), subject, true), SEEN))
    .chain([(18, heads("g@loom.example.test", "not read yet", true), &[][..])])
    .collect();
    more(&world, &mail);
    let plan = sweeps(&world, true).run(None, true, None).expect("planned");
    let sender = |who: &str, n: usize| json!({"from_addr": format!("{who}@loom.example.test"), "count": n});
    assert_eq!(
        plan["results"][0]["going"][0],
        json!({"bucket": "newsletters/marketing", "count": 9,
               "senders": [sender("b", 2), sender("news", 2), sender("a", 1), sender("c", 1), sender("d", 1)],
               "subjects": ["issue 2", "weekly", "autumn", "summer", "sale"]})
    );
    assert_eq!(plan["results"][0]["going"][1]["bucket"], json!("automated-notifications"));
    assert_eq!(plan["results"][0]["going"].as_array().map(Vec::len), Some(2));
}

#[test]
fn a_sweep_moves_to_the_trash_the_server_lists_where_the_accounts_own_isnt() {
    let world = world();
    world.0.inbox.lock().expect("lock").as_mut().expect("open").trash_listed = Some("Deleted Messages".into());
    sweeps(&world, true).run(None, false, None).expect("swept");
    let inbox = world.0.inbox.lock().expect("lock").take().expect("closed");
    assert_eq!(inbox.moves, [(vec![2, 3], "Deleted Messages".to_owned())]);
}

#[test]
fn a_sweeps_way_back_looks_for_its_mail_in_the_trash_it_went_to() {
    let world = world();
    world.0.inbox.lock().expect("lock").as_mut().expect("open").trash_listed = Some("Deleted Messages".into());
    sweeps(&world, true).run(None, false, None).expect("swept");
    let pushed = world.0.pushed.lock().expect("lock").clone();
    assert_eq!(pushed.len(), 2);
    assert!(pushed.iter().all(|e| e["dest_folder"] == "Deleted Messages"), "{pushed:?}");
}

#[test]
fn a_server_whose_folders_cant_be_read_moves_nothing_and_leaves_the_stack_as_it_was() {
    let world = world();
    world.0.inbox.lock().expect("lock").as_mut().expect("open").lost_listing = true;
    let (said, notes) = stopped(&world);
    assert_eq!(said, (json!("ConnectionLost"), json!(0), Value::Null));
    assert_eq!(notes, Vec::<String>::new());
    let inbox = world.0.inbox.lock().expect("lock").take().expect("closed");
    assert_eq!((inbox.moves.len(), inbox.closed), (0, Some(true)));
}

#[test]
fn a_read_only_process_only_plans() {
    let world = world();
    assert_eq!(sweeps(&world, false).run(None, false, None), Err(Failure::Fault(READ_ONLY)));
    assert!(sweeps(&world, false).run(None, true, None).is_ok());
}

#[test]
fn an_account_that_isnt_one_is_refused() {
    let refused = sweeps(&world(), true).run(Some("bea@example.test"), true, None);
    assert!(matches!(refused, Err(Failure::Problem(p)) if p.kind == Kind::Refused && p.said == "unknown account"));
}

#[test]
fn a_server_that_cant_be_reached_says_its_class_alone() {
    let world = Shared(Arc::new(World { refuse_open: true, ..World::default() }));
    let done = sweeps(&world, true).run(Some("ada@example.test"), false, None).expect("swept");
    assert_eq!(done["results"], json!([{"account": "ada@example.test", "error": "ConnectionRefused", "planned": 0}]));
    assert_eq!(done["undo_group"], Value::Null);
}
