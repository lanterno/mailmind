//! The folder rail: roles, its order, and what it serves when the server is slow to change or can't be reached.

#![allow(clippy::unwrap_used)]

use std::collections::BTreeSet;
use std::sync::atomic::{AtomicI64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use mm_kernel::errors::{Failure, Fault, Kind};
use mm_kernel::seams::{Clock, Moment};
use mm_mailbox::folders::Folders;
use mm_mailbox::ports::{Accounts, MailSession, MailSessions, SessionError, StoredMail, ViewCounts};
use mm_mailbox::roles::{self, Folder, RailFolder};

#[derive(Debug)]
struct Ada;

impl Accounts for Ada {
    fn known(&self, account: &str) -> Result<bool, Fault> {
        Ok(account == "ada@example.test")
    }
}

#[derive(Debug, Default)]
struct Stored;

impl StoredMail for Stored {
    fn stored_folder_counts(&self, _account: &str) -> Result<Vec<(String, Option<i64>, i64)>, Fault> {
        Ok(vec![("Trash".into(), None, 1), ("Archive".into(), Some(0), 3)])
    }

    fn view_counts(&self, _accounts: &[String]) -> Result<ViewCounts, Fault> {
        Ok(ViewCounts { snoozed: 1, muted: 2, waiting: 3, replylater: 4 })
    }
}

#[derive(Debug, Default)]
struct Ticking(AtomicI64);

impl Ticking {
    fn pass(&self, seconds: i64) {
        self.0.fetch_add(seconds * 1_000_000, Ordering::SeqCst);
    }
}

impl Clock for Ticking {
    fn now(&self) -> Moment {
        Moment::from_micros(self.0.load(Ordering::SeqCst))
    }
}

fn folder(name: &str, attributes: &[&str]) -> Folder {
    Folder {
        name: name.into(),
        attributes: attributes.iter().map(|a| (*a).to_owned()).collect::<BTreeSet<_>>(),
        delimiter: Some("/".into()),
    }
}

struct Session;

impl MailSession for Session {
    fn folders(&mut self) -> Result<Vec<Folder>, SessionError> {
        Ok(vec![
            folder("Trash", &[]),
            folder("Clients", &[]),
            folder("INBOX", &[]),
            folder("Saved", &["\\sent"]),
            folder("Sent", &[]),
            folder("Folders", &["\\noselect"]),
        ])
    }

    fn status(&mut self, folder: &str) -> Result<(i64, i64), SessionError> {
        match folder {
            "INBOX" => Ok((10, 12)),
            "Clients" => Err(SessionError::Unreachable("Timeout")),
            _ => Ok((0, 1)),
        }
    }

    fn logout(&mut self) {}
}

/// Each `open` takes the next of what it was given: a session, or why not.
#[derive(Debug)]
struct Server {
    opened: AtomicUsize,
    answers: Mutex<Vec<Result<(), SessionError>>>,
}

impl Server {
    fn answering(answers: Vec<Result<(), SessionError>>) -> Arc<Server> {
        Arc::new(Server { opened: AtomicUsize::new(0), answers: Mutex::new(answers) })
    }
}

impl MailSessions for Server {
    fn open(&self, _account: &str) -> Result<Box<dyn MailSession>, SessionError> {
        self.opened.fetch_add(1, Ordering::SeqCst);
        let mut answers = self.answers.lock().unwrap();
        let answer = if answers.is_empty() { Ok(()) } else { answers.remove(0) };
        answer.map(|()| Box::new(Session) as Box<dyn MailSession>)
    }
}

fn rail(folders: &Folders) -> Vec<(String, &'static str, i64, i64, bool)> {
    let found = folders.of_account("ada@example.test").unwrap();
    found.folders.into_iter().map(|(f, takes)| (f.name, f.role, f.unseen, f.total, takes)).collect()
}

fn folders(server: &Arc<Server>, clock: &Arc<Ticking>) -> Folders {
    Folders::new(Arc::new(Ada), server.clone(), Arc::new(Stored), clock.clone())
}

#[test]
fn the_rail_is_each_selectable_folder_in_its_place_with_its_counts() {
    let (server, clock) = (Server::answering(vec![]), Arc::new(Ticking::default()));
    let folders = folders(&server, &clock);
    assert_eq!(
        rail(&folders),
        [
            ("INBOX".to_owned(), "inbox", 10, 12, true),
            ("Saved".to_owned(), "sent", 0, 1, false),
            ("Clients".to_owned(), "custom", 0, 0, true),
            ("Sent".to_owned(), "custom", 0, 1, true),
            ("Trash".to_owned(), "trash", 0, 1, true),
        ]
    );
    let found = folders.of_account("ada@example.test").unwrap();
    assert_eq!(found.counts, ViewCounts { snoozed: 1, muted: 2, waiting: 3, replylater: 4 });
    assert_eq!(found.account, "ada@example.test");
}

#[test]
fn an_account_not_saved_is_refused() {
    let folders = folders(&Server::answering(vec![]), &Arc::new(Ticking::default()));
    let Err(Failure::Problem(problem)) = folders.of_account("nobody@example.test") else { panic!("not refused") };
    assert_eq!((problem.kind, problem.said.as_str()), (Kind::Refused, "unknown account"));
}

#[test]
fn the_list_is_kept_for_a_few_seconds_then_asked_again() {
    let (server, clock) = (Server::answering(vec![]), Arc::new(Ticking::default()));
    let folders = folders(&server, &clock);
    rail(&folders);
    clock.pass(24);
    rail(&folders);
    assert_eq!(server.opened.load(Ordering::SeqCst), 1);
    clock.pass(1);
    rail(&folders);
    assert_eq!(server.opened.load(Ordering::SeqCst), 2);
    folders.invalidate(Some("ada@example.test")).unwrap();
    rail(&folders);
    assert_eq!(server.opened.load(Ordering::SeqCst), 3);
}

#[test]
fn a_server_out_of_reach_gives_the_folders_mail_is_stored_in() {
    let server = Server::answering(vec![Err(SessionError::Unreachable("ConnectionRefused"))]);
    let folders = folders(&server, &Arc::new(Ticking::default()));
    assert_eq!(
        rail(&folders),
        [
            ("INBOX".to_owned(), "inbox", 0, 0, true),
            ("Archive".to_owned(), "archive", 0, 3, true),
            ("Trash".to_owned(), "trash", 0, 1, true),
        ]
    );
}

#[test]
fn a_failed_refresh_serves_the_list_kept_however_old() {
    let failed = SessionError::Failed(Fault::new("Abort"));
    let (server, clock) = (Server::answering(vec![Ok(()), Err(failed)]), Arc::new(Ticking::default()));
    let folders = folders(&server, &clock);
    let first = rail(&folders);
    clock.pass(3600);
    assert_eq!(rail(&folders), first);
    assert_eq!(server.opened.load(Ordering::SeqCst), 2);
}

#[test]
fn a_failure_with_nothing_kept_is_a_fault() {
    let server = Server::answering(vec![Err(SessionError::Failed(Fault::new("Abort")))]);
    let folders = folders(&server, &Arc::new(Ticking::default()));
    assert_eq!(folders.of_account("ada@example.test").unwrap_err(), Failure::Fault(Fault::new("Abort")));
}

#[test]
fn roles_come_from_the_name_inbox_then_attributes_then_names() {
    assert_eq!(roles::role_by_name("inbox", None), "inbox");
    assert_eq!(roles::role_by_name("[Gmail]/Sent Mail", Some("/")), "sent");
    assert_eq!(roles::role_by_name("INBOX.Deleted Items", None), "trash");
    assert_eq!(roles::role_by_name("Labels/Important", Some("/")), "custom");
    assert_eq!(roles::role_of(&folder("Stuff", &["\\junk"])), "junk");
    let named = |name: &str, role| RailFolder { name: name.into(), role, unseen: 0, total: 0 };
    let ordered = roles::rail_order(vec![named("b", "custom"), named("Trash", "trash"), named("A", "custom")]);
    let names: Vec<&str> = ordered.iter().map(|f| f.name.as_str()).collect();
    assert_eq!(names, ["A", "b", "Trash"]);
    assert!(!roles::takes_filing("drafts") && !roles::takes_filing("all") && roles::takes_filing("junk"));
}
