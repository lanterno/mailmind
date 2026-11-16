//! Each account's connection kept between the jobs that hold its gate (#5), over servers that write down what each
//! connection was asked: lent only to a job holding the gate and only once at a time, checked with a NOOP before each
//! lending, replaced when it has served long enough for its provider, closed when it fails, on a wake, when its
//! account changes and on a quit; and a lending that starts with no folder selected.

#![allow(clippy::unwrap_used)]

use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::atomic::{AtomicI64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::ThreadId;

use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Moment};
use mm_mailbox::gates::{Gates, Priority, Refused, Wait, on_gate};
use mm_mailbox::kept::{Counts, Keeping, KeptAccounts, KeptParts, KeptServers, SlotStats};
use mm_mailbox::ports::{MailSession, SessionError};
use mm_mailbox::roles::Folder;
use mm_mailbox::session::{Headers, MailServer, MailServers, Search, Selected, ServerError};

const ADA: &str = "ada@example.test";
const BEA: &str = "bea@example.test";
/// An account on Proton Bridge.
const PIP: &str = "pip@example.test";
/// An account the configuration no longer holds.
const GONE: &str = "gone@example.test";

/// What every connection was asked, `<connection> <what>`, in order.
type Log = Arc<Mutex<Vec<String>>>;

#[derive(Debug, Default)]
struct Trouble {
    /// A NOOP finds the connection gone.
    noop_lost: bool,
    /// The server offers CONDSTORE.
    condstore: bool,
}

struct Server {
    id: usize,
    log: Log,
    trouble: Arc<Mutex<Trouble>>,
}

impl Server {
    fn said(&self, what: impl Into<String>) {
        self.log.lock().unwrap().push(format!("{} {}", self.id, what.into()));
    }
}

impl MailSession for Server {
    fn folders(&mut self) -> Result<Vec<Folder>, SessionError> {
        Ok(Vec::new())
    }

    fn status(&mut self, _folder: &str) -> Result<(i64, i64), SessionError> {
        Ok((0, 0))
    }

    fn logout(&mut self) {
        self.said("LOGOUT");
    }
}

impl MailServer for Server {
    fn capabilities(&self) -> Vec<String> {
        vec!["IMAP4rev1".to_owned()]
    }

    fn select(&mut self, folder: &str, _readonly: bool) -> Result<Selected, ServerError> {
        self.said(format!("SELECT {folder}"));
        if folder == "Nowhere" { Err(ServerError::Refused("error")) } else { Ok(Selected::default()) }
    }

    fn search(&mut self, _criteria: Search<'_>) -> Result<Vec<u32>, ServerError> {
        self.said("SEARCH");
        Ok(vec![1])
    }

    fn flags(&mut self, _uids: &[u32]) -> Result<BTreeMap<u32, Vec<String>>, ServerError> {
        Ok(BTreeMap::new())
    }

    fn enable_condstore(&mut self) -> Result<bool, ServerError> {
        self.said("ENABLE");
        Ok(self.trouble.lock().unwrap().condstore)
    }

    fn headers(&mut self, _uids: &[u32], _fields: &[&str], _gmail: bool) -> Result<Vec<Headers>, ServerError> {
        Ok(Vec::new())
    }

    fn body(&mut self, uid: u32) -> Result<Option<Vec<u8>>, ServerError> {
        self.said(format!("BODY {uid}"));
        match uid {
            // The connection goes over this one.
            66 => Err(ServerError::Unreachable("ConnectionLost")),
            // The server refuses this one, and the connection is fine.
            77 => Err(ServerError::Refused("error")),
            _ => Ok(Some(b"Subject: hi\r\n\r\nhello\r\n".to_vec())),
        }
    }

    fn arrivals(&mut self, _uids: &[u32]) -> Result<BTreeMap<u32, i64>, ServerError> {
        Ok(BTreeMap::new())
    }

    fn store_flags(&mut self, _: &[u32], _: bool, _: &[&str]) -> Result<(), ServerError> {
        self.said("STORE");
        Ok(())
    }

    fn store_labels(&mut self, _: &[u32], _: bool, _: &[&str]) -> Result<(), ServerError> {
        Ok(())
    }

    fn copy(&mut self, _: &[u32], _: &str) -> Result<(), ServerError> {
        Ok(())
    }

    fn uid_move(&mut self, _: &[u32], dest: &str) -> Result<(), ServerError> {
        self.said(format!("MOVE {dest}"));
        Ok(())
    }

    fn expunge(&mut self, _: &[u32]) -> Result<(), ServerError> {
        self.said("EXPUNGE");
        Ok(())
    }

    fn append(&mut self, folder: &str, _: &[u8], _: &[&str], _: Option<&str>) -> Result<Option<u32>, ServerError> {
        self.said(format!("APPEND {folder}"));
        Ok(None)
    }

    fn shutdown(&mut self) {
        self.said("SHUTDOWN");
    }

    fn noop(&mut self) -> Result<(), ServerError> {
        self.said("NOOP");
        if self.trouble.lock().unwrap().noop_lost { Err(ServerError::Unreachable("ConnectionLost")) } else { Ok(()) }
    }

    fn set_timeout(&mut self, seconds: u64) {
        self.said(format!("TIMEOUT {seconds}"));
    }
}

/// Each new connection logs in; the log says which account's.
#[derive(Debug, Default)]
struct Opener {
    log: Log,
    opened: AtomicUsize,
    trouble: Arc<Mutex<Trouble>>,
}

impl MailServers for Opener {
    fn open(&self, account: &str, timeout_seconds: u64) -> Result<Box<dyn MailServer>, ServerError> {
        let id = self.opened.fetch_add(1, Ordering::SeqCst) + 1;
        self.log.lock().unwrap().push(format!("{id} LOGIN {account} {timeout_seconds}"));
        Ok(Box::new(Server { id, log: self.log.clone(), trouble: self.trouble.clone() }))
    }
}

/// A gate for each account, with the jobs that hold one on each thread: what `holds` asks.
#[derive(Debug, Default)]
struct ThreadGates {
    holding: Mutex<HashMap<ThreadId, HashSet<String>>>,
}

impl Gates for ThreadGates {
    fn run(&self, account: &str, _priority: Priority, _wait: Wait, job: &mut dyn FnMut()) -> Result<(), Refused> {
        let me = std::thread::current().id();
        self.holding.lock().unwrap().entry(me).or_default().insert(account.to_owned());
        job();
        self.holding.lock().unwrap().entry(me).or_default().remove(account);
        Ok(())
    }

    fn held(&self, account: &str) -> bool {
        self.holding.lock().unwrap().values().any(|held| held.contains(account))
    }

    fn holds(&self, account: &str) -> bool {
        let me = std::thread::current().id();
        self.holding.lock().unwrap().get(&me).is_some_and(|held| held.contains(account))
    }
}

#[derive(Debug)]
struct Accounts;

impl KeptAccounts for Accounts {
    fn keeping(&self, account: &str) -> Result<Option<Keeping>, Fault> {
        Ok(match account {
            ADA | BEA => Some(Keeping::USUAL),
            PIP => Some(Keeping::BRIDGE),
            _ => None,
        })
    }
}

/// A clock the test moves, in seconds.
#[derive(Debug, Default)]
struct Hands(AtomicI64);

impl Hands {
    fn on(&self, seconds: i64) {
        self.0.fetch_add(seconds, Ordering::SeqCst);
    }
}

impl Clock for Hands {
    fn now(&self) -> Moment {
        Moment::from_micros((1_793_620_800 + self.0.load(Ordering::SeqCst)) * 1_000_000)
    }
}

struct Keeper {
    kept: KeptServers,
    gates: Arc<ThreadGates>,
    opener: Arc<Opener>,
    clock: Arc<Hands>,
}

fn keeper() -> Keeper {
    let (gates, opener, clock) =
        (Arc::new(ThreadGates::default()), Arc::new(Opener::default()), Arc::new(Hands::default()));
    let kept = KeptServers::new(KeptParts {
        opener: opener.clone(),
        gates: gates.clone(),
        accounts: Arc::new(Accounts),
        clock: clock.clone(),
    });
    Keeper { kept, gates, opener, clock }
}

impl Keeper {
    /// `work` on a session of `account`'s, as a job holding its gate runs it, which logs out after.
    fn job<T>(&self, account: &str, work: impl FnOnce(&mut dyn MailServer) -> T) -> T {
        on_gate(&*self.gates, account, Priority::Interactive, Wait::Ever, || {
            let mut s = self.kept.open(account, 60).unwrap();
            let answer = work(&mut *s);
            s.logout();
            answer
        })
        .unwrap()
    }

    /// What was asked since `from`, in order.
    fn log(&self, from: usize) -> Vec<String> {
        self.opener.log.lock().unwrap()[from..].to_vec()
    }

    fn mark(&self) -> usize {
        self.opener.log.lock().unwrap().len()
    }

    fn opened(&self) -> usize {
        self.opener.opened.load(Ordering::SeqCst)
    }

    fn trouble(&self) -> std::sync::MutexGuard<'_, Trouble> {
        self.opener.trouble.lock().unwrap()
    }
}

fn read(s: &mut dyn MailServer, uid: u32) -> Result<Option<Vec<u8>>, ServerError> {
    s.select("INBOX", true)?;
    s.body(uid)
}

#[test]
fn one_job_after_another_on_an_accounts_gate_uses_one_connection() {
    let k = keeper();
    k.job(ADA, |s| read(s, 1).unwrap());
    k.job(ADA, |s| read(s, 2).unwrap());
    k.job(ADA, |s| s.select("Archive", false).and_then(|_| s.uid_move(&[2], "Trash")).unwrap());
    assert_eq!(k.opened(), 1);
    assert_eq!(
        k.log(0),
        [
            "1 LOGIN ada@example.test 60",
            "1 ENABLE",
            "1 SELECT INBOX",
            "1 BODY 1",
            // Checked before the second job, then given the time limit it asks for.
            "1 TIMEOUT 10",
            "1 NOOP",
            "1 TIMEOUT 60",
            "1 SELECT INBOX",
            "1 BODY 2",
            "1 TIMEOUT 10",
            "1 NOOP",
            "1 TIMEOUT 60",
            "1 SELECT Archive",
            "1 MOVE Trash",
        ]
    );
    assert_eq!(k.kept.stats().counts, Counts { created: 1, reused: 2, validate_fail: 0, discard_dirty: 0 });
    assert_eq!(k.kept.stats().slots, [SlotStats { open: true, lendings: 3, age: Some(0.0) }]);
}

#[test]
fn each_account_keeps_its_own() {
    let k = keeper();
    k.job(ADA, |s| read(s, 1).unwrap());
    k.job(BEA, |s| read(s, 1).unwrap());
    k.job(ADA, |s| read(s, 2).unwrap());
    k.job(BEA, |s| read(s, 2).unwrap());
    assert_eq!(k.opened(), 2);
    let ada: Vec<String> = k.log(0).into_iter().filter(|l| l.ends_with("BODY 2")).collect();
    assert_eq!(ada, ["1 BODY 2", "2 BODY 2"]);
}

#[test]
fn a_session_opened_without_the_gate_logs_in_and_out_as_before() {
    let k = keeper();
    for uid in [1, 2] {
        let mut s = k.kept.open(ADA, 30).unwrap();
        read(&mut *s, uid).unwrap();
        s.logout();
    }
    assert_eq!(k.opened(), 2);
    assert_eq!(
        k.log(0),
        [
            "1 LOGIN ada@example.test 30",
            "1 SELECT INBOX",
            "1 BODY 1",
            "1 LOGOUT",
            "2 LOGIN ada@example.test 30",
            "2 SELECT INBOX",
            "2 BODY 2",
            "2 LOGOUT"
        ]
    );
    assert_eq!(k.kept.stats().counts, Counts::default());
}

#[test]
fn an_account_the_configuration_no_longer_holds_keeps_none() {
    let k = keeper();
    k.job(GONE, |s| read(s, 1).unwrap());
    k.job(GONE, |s| read(s, 1).unwrap());
    assert_eq!(k.opened(), 2);
    assert!(k.log(0).contains(&"2 LOGOUT".to_owned()));
}

#[test]
fn a_second_session_in_one_job_is_its_own_and_logs_out() {
    let k = keeper();
    k.job(ADA, |s| {
        s.select("Drafts", false).unwrap();
        let mut other = k.kept.open(ADA, 60).unwrap();
        other.select("INBOX", true).unwrap();
        other.logout();
        s.append("Drafts", b"x", &[], None).unwrap();
    });
    assert_eq!(k.opened(), 2);
    assert_eq!(
        k.log(0),
        [
            "1 LOGIN ada@example.test 60",
            "1 ENABLE",
            "1 SELECT Drafts",
            "2 LOGIN ada@example.test 60",
            "2 SELECT INBOX",
            "2 LOGOUT",
            "1 APPEND Drafts",
        ]
    );
}

#[test]
fn a_connection_still_out_after_its_job_is_never_lent_to_the_next() {
    let k = keeper();
    let mut out = on_gate(&*k.gates, ADA, Priority::Interactive, Wait::Ever, || k.kept.open(ADA, 60).unwrap()).unwrap();
    // The next job, on another thread, while the first job's session is still held.
    std::thread::scope(|scope| {
        scope.spawn(|| k.job(ADA, |s| read(s, 2).unwrap())).join().unwrap();
    });
    assert_eq!(k.opened(), 2);
    out.select("INBOX", true).unwrap();
    out.logout();
    assert!(k.log(0).contains(&"2 LOGOUT".to_owned()), "the second job's own connection logged out");
    // Back now: the next job has it.
    k.job(ADA, |s| read(s, 3).unwrap());
    assert_eq!(k.opened(), 2);
}

#[test]
fn a_lending_starts_with_no_folder_selected() {
    let k = keeper();
    k.job(ADA, |s| s.select("Archive", false).unwrap());
    let mark = k.mark();
    // The next job's command on a folder, before it selects one, is refused as a new connection would refuse it,
    // and never reaches the folder the job before left selected.
    let refused = k.job(ADA, |s| (s.expunge(&[4]), s.store_flags(&[4], true, &["\\Seen"]), s.search(Search::All)));
    assert_eq!(
        refused,
        (Err(ServerError::Refused("error")), Err(ServerError::Refused("error")), Err(ServerError::Refused("error")))
    );
    assert!(!k.log(mark).iter().any(|l| l.contains("EXPUNGE") || l.contains("STORE") || l.contains("SEARCH")));
    // And a refusal leaves the connection kept.
    assert_eq!(k.opened(), 1);
}

#[test]
fn a_select_refused_leaves_no_folder_selected() {
    let k = keeper();
    let answer = k.job(ADA, |s| {
        s.select("INBOX", false).unwrap();
        assert!(s.select("Nowhere", false).is_err());
        s.expunge(&[4])
    });
    assert_eq!(answer, Err(ServerError::Refused("error")));
    assert!(!k.log(0).iter().any(|l| l.contains("EXPUNGE")));
}

#[test]
fn a_connection_lost_while_lent_is_closed_not_kept() {
    let k = keeper();
    let lost = k.job(ADA, |s| read(s, 66));
    assert_eq!(lost, Err(ServerError::Unreachable("ConnectionLost")));
    assert!(k.log(0).contains(&"1 SHUTDOWN".to_owned()));
    k.job(ADA, |s| read(s, 1).unwrap());
    assert_eq!(k.opened(), 2);
    assert_eq!(k.kept.stats().counts.discard_dirty, 1);
}

#[test]
fn a_refusal_while_lent_keeps_the_connection() {
    let k = keeper();
    assert_eq!(k.job(ADA, |s| read(s, 77)), Err(ServerError::Refused("error")));
    k.job(ADA, |s| read(s, 1).unwrap());
    assert_eq!(k.opened(), 1);
}

#[test]
fn a_job_that_panics_leaves_its_connection_closed() {
    let k = keeper();
    let panicked = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        k.job(ADA, |s| {
            s.select("INBOX", true).unwrap();
            panic!("a bug");
        })
    }));
    assert!(panicked.is_err());
    assert!(k.log(0).contains(&"1 SHUTDOWN".to_owned()));
    k.job(ADA, |s| read(s, 1).unwrap());
    assert_eq!(k.opened(), 2);
}

#[test]
fn a_session_the_borrower_shut_is_closed_not_kept() {
    let k = keeper();
    on_gate(&*k.gates, ADA, Priority::Interactive, Wait::Ever, || {
        let mut s = k.kept.open(ADA, 60).unwrap();
        s.shutdown();
    })
    .unwrap();
    k.job(ADA, |s| read(s, 1).unwrap());
    assert_eq!(k.opened(), 2);
}

#[test]
fn a_kept_connection_that_doesnt_answer_its_noop_is_replaced() {
    let k = keeper();
    k.job(ADA, |s| read(s, 1).unwrap());
    // The server dropped it while it sat: an idle timeout, or a sleep.
    k.trouble().noop_lost = true;
    let mark = k.mark();
    k.job(ADA, |s| read(s, 2).unwrap());
    assert_eq!(
        k.log(mark),
        [
            "1 TIMEOUT 10",
            "1 NOOP",
            "1 SHUTDOWN",
            "2 LOGIN ada@example.test 60",
            "2 ENABLE",
            "2 SELECT INBOX",
            "2 BODY 2"
        ]
    );
    assert_eq!(k.kept.stats().counts, Counts { created: 2, reused: 0, validate_fail: 1, discard_dirty: 0 });
}

#[test]
fn a_connection_is_replaced_after_ten_minutes_or_200_jobs() {
    let k = keeper();
    k.job(ADA, |s| read(s, 1).unwrap());
    k.clock.on(600);
    k.job(ADA, |s| read(s, 1).unwrap());
    assert_eq!(k.opened(), 1, "ten minutes is still young enough");
    k.clock.on(1);
    let mark = k.mark();
    k.job(ADA, |s| read(s, 1).unwrap());
    // Logged out, politely and briefly, before the new one.
    assert_eq!(&k.log(mark)[..3], ["1 TIMEOUT 3", "1 LOGOUT", "2 LOGIN ada@example.test 60"]);
    for _ in 0..199 {
        k.job(ADA, |s| read(s, 1).unwrap());
    }
    assert_eq!(k.opened(), 2);
    k.job(ADA, |s| read(s, 1).unwrap());
    assert_eq!(k.opened(), 3, "the 200th lending was its last");
}

#[test]
fn a_bridge_connection_is_replaced_after_90_seconds_or_40_jobs() {
    let k = keeper();
    k.job(PIP, |s| read(s, 1).unwrap());
    k.clock.on(91);
    k.job(PIP, |s| read(s, 1).unwrap());
    assert_eq!(k.opened(), 2);
    for _ in 0..39 {
        k.job(PIP, |s| read(s, 1).unwrap());
    }
    assert_eq!(k.opened(), 2);
    k.job(PIP, |s| read(s, 1).unwrap());
    assert_eq!(k.opened(), 3);
    assert_eq!(Keeping::BRIDGE, Keeping { seconds: 90, lendings: 40 });
    assert_eq!(Keeping::USUAL, Keeping { seconds: 600, lendings: 200 });
}

#[test]
fn condstore_is_enabled_as_the_connection_opens_and_told_to_each_job() {
    let k = keeper();
    k.trouble().condstore = true;
    assert!(k.job(ADA, |s| s.enable_condstore().unwrap()));
    assert!(k.job(ADA, |s| s.enable_condstore().unwrap()));
    let enables: Vec<String> = k.log(0).into_iter().filter(|l| l.ends_with("ENABLE")).collect();
    assert_eq!(enables, ["1 ENABLE"], "ENABLE goes once, before any SELECT");
}

#[test]
fn a_wake_closes_every_kept_connection_and_one_lent_as_it_comes_back() {
    let k = keeper();
    k.job(ADA, |s| read(s, 1).unwrap());
    let mark = k.mark();
    on_gate(&*k.gates, BEA, Priority::Interactive, Wait::Ever, || {
        let mut s = k.kept.open(BEA, 60).unwrap();
        read(&mut *s, 1).unwrap();
        k.kept.woke();
        // Ada's, sitting, closed at once and without a word to its server, which may be gone.
        assert_eq!(k.log(mark).last().unwrap(), "1 SHUTDOWN");
        s.logout();
    })
    .unwrap();
    assert_eq!(k.log(mark).last().unwrap(), "2 SHUTDOWN");
    let mark = k.mark();
    k.job(ADA, |s| read(s, 1).unwrap());
    assert_eq!(k.log(mark)[0], "3 LOGIN ada@example.test 60", "no NOOP on a connection gone");
}

#[test]
fn an_account_changed_or_removed_has_its_connection_logged_out() {
    let k = keeper();
    k.job(ADA, |s| read(s, 1).unwrap());
    k.job(BEA, |s| read(s, 1).unwrap());
    let mark = k.mark();
    k.kept.forget(ADA);
    assert_eq!(k.log(mark), ["1 TIMEOUT 3", "1 LOGOUT"]);
    k.job(BEA, |s| read(s, 2).unwrap());
    assert_eq!(k.opened(), 2, "Bea's is kept");
    // One lent as its account goes is logged out as it comes back.
    on_gate(&*k.gates, BEA, Priority::Interactive, Wait::Ever, || {
        let mut s = k.kept.open(BEA, 60).unwrap();
        k.kept.forget(BEA);
        let mark = k.mark();
        s.logout();
        assert_eq!(k.log(mark), ["2 TIMEOUT 3", "2 LOGOUT"]);
    })
    .unwrap();
    assert!(k.kept.stats().slots.is_empty());
}

#[test]
fn a_quit_logs_each_connection_out_and_keeps_none_after() {
    let k = keeper();
    k.job(ADA, |s| read(s, 1).unwrap());
    k.job(BEA, |s| read(s, 1).unwrap());
    let mark = k.mark();
    k.kept.close();
    let mut closed = k.log(mark);
    closed.sort();
    assert_eq!(closed, ["1 LOGOUT", "1 TIMEOUT 3", "2 LOGOUT", "2 TIMEOUT 3"]);
    let mark = k.mark();
    k.job(ADA, |s| read(s, 1).unwrap());
    assert_eq!(k.log(mark), ["3 LOGIN ada@example.test 60", "3 SELECT INBOX", "3 BODY 1", "3 LOGOUT"]);
}

#[test]
fn what_it_counts_names_no_account() {
    let k = keeper();
    k.job(BEA, |s| read(s, 1).unwrap());
    k.clock.on(5);
    k.job(ADA, |s| read(s, 1).unwrap());
    k.clock.on(2);
    k.job(ADA, |s| read(s, 1).unwrap());
    let stats = k.kept.stats();
    // In the accounts' order: Ada's, then Bea's.
    assert_eq!(
        stats.slots,
        [SlotStats { open: true, lendings: 2, age: Some(2.0) }, SlotStats { open: true, lendings: 1, age: Some(7.0) }]
    );
    assert!(!format!("{stats:?}").contains("example.test"));
}
