//! The newest Tidy run a person can still take back, from the undo stack; and a run by itself
//! (`tests/unit/mailbox/test_tidying.py`'s): once a day from six, what the brief's Tidy offers, archived as one group.

use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::{Arc, Mutex};

use mm_kernel::errors::{Failure, Fault};
use mm_kernel::seams::{Clock, Moment};
use mm_kernel::zone::Zone;
use mm_mailbox::filing::NamedCopy;
use mm_mailbox::ports::{DigestReads, Row, StoredDigest};
use mm_mailbox::tidy::{Entry, Filed, RunFiling, TakenBack, TidyLog, UndoEntries, group_of, is_run, latest, takes};
use mm_mailbox::tidying::{Tidying, TidyingParts};
use serde_json::{Value, json};

#[derive(Debug)]
struct Stack(Vec<Entry>);

impl UndoEntries for Stack {
    fn entries(&self) -> Result<Vec<Entry>, Fault> {
        Ok(self.0.clone())
    }
}

fn filed(subject: &str) -> Filed {
    Filed { subject: Some(subject.to_owned()), who: Some("Lin".to_owned()) }
}

fn entry(group: Option<&str>, subjects: &[&str]) -> Entry {
    Entry { group: group.map(str::to_owned), children: subjects.iter().map(|s| filed(s)).collect() }
}

#[test]
fn a_run_is_a_group_named_for_its_day() {
    assert!(is_run(Some("tidy-2026-11-02")));
    assert!(!is_run(Some("bulk-1")) && !is_run(None));
}

#[test]
fn the_newest_run_with_what_it_filed_the_last_first() {
    let stack = Stack(vec![
        entry(Some("tidy-2026-11-01"), &["old"]),
        entry(Some("tidy-2026-11-02"), &["first", "second"]),
        entry(Some("bulk-1"), &["a person's"]),
        entry(None, &[]),
    ]);
    let run = latest(&stack).expect("read").expect("a run");
    assert_eq!(run.group, "tidy-2026-11-02");
    assert_eq!(run.filed, [filed("second"), filed("first")]);
}

#[test]
fn with_no_run_on_the_stack_there_is_none() {
    assert_eq!(latest(&Stack(vec![entry(Some("bulk-1"), &["x"])])).expect("read"), None);
}

// ── a run by itself ─────────────────────────────────────────────────────────────────────────────────

/// Monday 2 November 2026, midnight UTC.
const MONDAY: i64 = 1_793_577_600;
const ADA: &str = "ada@example.test";

#[derive(Debug)]
struct Frozen(AtomicI64);

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(self.0.load(Ordering::SeqCst) * 1_000_000)
    }
}

/// A zone this many seconds east of UTC.
#[derive(Debug)]
struct East(i64);

impl Zone for East {
    fn offset_at(&self, _utc: i64) -> i64 {
        self.0
    }
}

/// The days that ran, what Undo took back, and the groups a run asked about.
#[derive(Debug, Default)]
struct Log {
    ran: Vec<String>,
    undone: TakenBack,
    asked: Mutex<Vec<String>>,
}

impl TidyLog for Log {
    fn ran_under(&self, mutation_id: &str) -> Result<bool, Fault> {
        self.asked.lock().expect("asked").push(mutation_id.to_owned());
        Ok(self.ran.iter().any(|r| r == mutation_id))
    }
    fn archives_undone(&self) -> Result<TakenBack, Fault> {
        Ok(self.undone.clone())
    }
}

/// The brief's list, and how many it was asked for each time.
#[derive(Debug, Default)]
struct Brief {
    rows: Vec<Row>,
    read: Mutex<Vec<i64>>,
}

impl DigestReads for Brief {
    fn digest(&self, limit: i64) -> Result<StoredDigest, Fault> {
        self.read.lock().expect("read").push(limit);
        Ok(StoredDigest { rows: self.rows.clone(), ..StoredDigest::default() })
    }
}

/// What a run archived, under its group.
type Archived = (Vec<(NamedCopy, bool)>, String);

/// What each run archived.
#[derive(Debug, Default)]
struct Filer(Mutex<Vec<Archived>>);

impl RunFiling for Filer {
    fn archive(&self, rows: &[(NamedCopy, bool)], group: &str) -> Result<Value, Failure> {
        self.0.lock().expect("filed").push((rows.to_vec(), group.to_owned()));
        Ok(json!({"ok": true}))
    }
}

fn row(value: Value) -> Row {
    value.as_object().cloned().expect("an object")
}

/// A newsletter in the Inbox the brief's Tidy offers, with `changes` made to it.
fn offered(key: i64, changes: Value) -> Row {
    let mut r = row(json!({"account": ADA, "folder": "INBOX", "message_key": key, "uid": key + 100,
                           "message_id": format!("<m{key}@example.test>"), "importance": 1, "category": "info",
                           "action": ""}));
    r.extend(row(changes));
    r
}

#[test]
fn a_run_files_what_the_brief_offers_from_an_inbox_and_never_what_a_person_took_back() {
    let none = TakenBack::new();
    assert!(takes(&offered(1, json!({})), &none));
    assert!(takes(&offered(1, json!({"importance": null, "category": "other"})), &none));
    assert!(takes(&offered(1, json!({"importance": 0})), &none));
    for changes in [
        json!({"folder": "Archive"}),
        json!({"message_key": null}),
        json!({"uid": null}),
        json!({"importance": 3}),
        json!({"action": "Reply"}),
        json!({"category": "personal"}),
        json!({"category": null}),
    ] {
        assert!(!takes(&offered(1, changes.clone()), &none), "{changes}");
    }
    let taken_back = TakenBack::from([(ADA.to_owned(), Some("<m1@example.test>".to_owned()))]);
    assert!(!takes(&offered(1, json!({})), &taken_back));
    assert!(takes(&offered(2, json!({})), &taken_back));
    assert_eq!(group_of("2026-11-02"), "tidy-2026-11-02");
}

struct Run {
    clock: Arc<Frozen>,
    log: Arc<Log>,
    brief: Arc<Brief>,
    filer: Arc<Filer>,
    tidying: Tidying,
}

/// A run at Monday 07:00 UTC, in a zone `east` seconds east of it.
fn run(log: Log, rows: Vec<Row>, east: i64) -> Run {
    let clock = Arc::new(Frozen(AtomicI64::new(MONDAY + 7 * 3600)));
    let (log, brief, filer) = (Arc::new(log), Arc::new(Brief { rows, ..Brief::default() }), Arc::new(Filer::default()));
    let tidying = Tidying::new(TidyingParts {
        log: log.clone(),
        digest: brief.clone(),
        filing: filer.clone(),
        clock: clock.clone(),
        zone: Arc::new(East(east)),
    });
    Run { clock, log, brief, filer, tidying }
}

#[test]
fn a_run_archives_what_it_takes_as_one_group_named_for_the_day() {
    let undone = TakenBack::from([(ADA.to_owned(), Some("<m3@example.test>".to_owned()))]);
    let rows =
        vec![offered(1, json!({})), offered(2, json!({"importance": 4})), offered(3, json!({})), offered(4, json!({}))];
    let r = run(Log { undone, ..Log::default() }, rows, 0);
    assert_eq!(r.tidying.round(), Ok(Some("tidy-2026-11-02".into())));
    assert_eq!(*r.brief.read.lock().expect("read"), [400]);
    let filed = r.filer.0.lock().expect("filed").clone();
    let picked = vec![((1, "INBOX".to_owned(), Some(101)), false), ((4, "INBOX".to_owned(), Some(104)), false)];
    assert_eq!(filed, [(picked, "tidy-2026-11-02".to_owned())]);
}

#[test]
fn a_run_waits_for_six_in_the_persons_zone_and_goes_once_a_day() {
    let r = run(Log::default(), vec![offered(1, json!({}))], 0);
    r.clock.0.store(MONDAY + 6 * 3600 - 1, Ordering::SeqCst);
    assert_eq!(r.tidying.round(), Ok(None));
    assert!(r.log.asked.lock().expect("asked").is_empty() && r.filer.0.lock().expect("filed").is_empty());
    let ran = run(Log { ran: vec!["tidy-2026-11-02".into()], ..Log::default() }, vec![offered(1, json!({}))], 0);
    assert_eq!(ran.tidying.round(), Ok(None));
    assert!(ran.brief.read.lock().expect("read").is_empty());
    // Sunday 23:00 UTC is Monday 07:00 eight hours east; Monday 08:00 UTC is 03:00 five hours west.
    let east = run(Log::default(), vec![offered(1, json!({}))], 8 * 3600);
    east.clock.0.store(MONDAY - 3600, Ordering::SeqCst);
    assert_eq!(east.tidying.round(), Ok(Some("tidy-2026-11-02".into())));
    let west = run(Log::default(), vec![offered(1, json!({}))], -5 * 3600);
    west.clock.0.store(MONDAY + 8 * 3600, Ordering::SeqCst);
    assert_eq!(west.tidying.round(), Ok(None));
}

#[test]
fn with_nothing_to_file_a_run_records_nothing_and_a_later_round_looks_again() {
    let r = run(Log::default(), vec![offered(1, json!({"category": "personal"}))], 0);
    assert_eq!(r.tidying.round(), Ok(None));
    assert_eq!(r.tidying.round(), Ok(None));
    assert_eq!(*r.log.asked.lock().expect("asked"), ["tidy-2026-11-02", "tidy-2026-11-02"]);
    assert!(r.filer.0.lock().expect("filed").is_empty());
}
