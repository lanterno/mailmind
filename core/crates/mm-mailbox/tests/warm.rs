//! Warming folders beyond the Inbox (`mailbox.application.warm`): whether an account is warmed (the variable when set,
//! else its entry in the file), which folders play its Drafts, Archive and Sent (found over one connection and kept),
//! and which of them each round syncs (Drafts every round, the archive every second, Sent every third; never Gmail's
//! All Mail, nor a part the Inbox plays). Against servers and a sync that write down what they were asked.

#![allow(clippy::unwrap_used)]

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use mm_kernel::errors::Fault;
use mm_mailbox::ingest::SyncError;
use mm_mailbox::ports::{MailSession, SessionError};
use mm_mailbox::roles::Folder;
use mm_mailbox::session::{Headers, MailServer, MailServers, Search, Selected, ServerError};
use mm_mailbox::warm::{WARM_RECENT, Warming, WarmingParts, Which};
use mm_mailbox::warm_ports::{WarmAccount, WarmAccounts, WarmSync};

const ADA: &str = "ada@example.test";
const BEA: &str = "bea@example.test";

fn folder(name: &str, attribute: &str) -> Folder {
    let attributes = [attribute].into_iter().filter(|a| !a.is_empty()).map(str::to_owned).collect();
    Folder { name: name.to_owned(), attributes, delimiter: Some("/".to_owned()) }
}

fn folders() -> Vec<Folder> {
    vec![
        folder("INBOX", ""),
        folder("Drafts", "\\drafts"),
        folder("Archive", "\\archive"),
        folder("All Mail", "\\all"),
        folder("Sent Items", "\\sent"),
    ]
}

/// How an account's server answers.
#[derive(Clone, Debug)]
enum Answers {
    /// LIST names these.
    Lists(Vec<Folder>),
    /// It refuses the LIST.
    Refuses,
    /// The connection goes during the LIST.
    LosesTheList,
    /// It can't be reached.
    Unreachable,
}

#[derive(Debug)]
struct Server {
    answers: Answers,
    asked: Arc<Mutex<Vec<String>>>,
}

impl MailSession for Server {
    fn folders(&mut self) -> Result<Vec<Folder>, SessionError> {
        self.asked.lock().unwrap().push("LIST".to_owned());
        match &self.answers {
            Answers::Lists(folders) => Ok(folders.clone()),
            Answers::LosesTheList => Err(SessionError::Unreachable("abort")),
            _ => Err(SessionError::Failed(Fault::new("error"))),
        }
    }

    fn status(&mut self, _folder: &str) -> Result<(i64, i64), SessionError> {
        Ok((0, 0))
    }

    fn logout(&mut self) {
        self.asked.lock().unwrap().push("LOGOUT".to_owned());
    }
}

const NOT_ASKED: ServerError = ServerError::Refused("not asked of warming");

impl MailServer for Server {
    fn capabilities(&self) -> Vec<String> {
        Vec::new()
    }

    fn select(&mut self, _folder: &str, _readonly: bool) -> Result<Selected, ServerError> {
        Err(NOT_ASKED)
    }

    fn search(&mut self, _criteria: Search<'_>) -> Result<Vec<u32>, ServerError> {
        Err(NOT_ASKED)
    }

    fn flags(&mut self, _uids: &[u32]) -> Result<BTreeMap<u32, Vec<String>>, ServerError> {
        Err(NOT_ASKED)
    }

    fn enable_condstore(&mut self) -> Result<bool, ServerError> {
        Err(NOT_ASKED)
    }

    fn headers(&mut self, _uids: &[u32], _fields: &[&str], _gmail: bool) -> Result<Vec<Headers>, ServerError> {
        Err(NOT_ASKED)
    }

    fn body(&mut self, _uid: u32) -> Result<Option<Vec<u8>>, ServerError> {
        Err(NOT_ASKED)
    }

    fn arrivals(&mut self, _uids: &[u32]) -> Result<BTreeMap<u32, i64>, ServerError> {
        Err(NOT_ASKED)
    }

    fn store_flags(&mut self, _: &[u32], _: bool, _: &[&str]) -> Result<(), ServerError> {
        Err(NOT_ASKED)
    }

    fn store_labels(&mut self, _: &[u32], _: bool, _: &[&str]) -> Result<(), ServerError> {
        Err(NOT_ASKED)
    }

    fn copy(&mut self, _: &[u32], _: &str) -> Result<(), ServerError> {
        Err(NOT_ASKED)
    }

    fn uid_move(&mut self, _: &[u32], _: &str) -> Result<(), ServerError> {
        Err(NOT_ASKED)
    }

    fn expunge(&mut self, _: &[u32]) -> Result<(), ServerError> {
        Err(NOT_ASKED)
    }

    fn append(&mut self, _: &str, _: &[u8], _: &[&str], _: Option<&str>) -> Result<Option<u32>, ServerError> {
        Err(NOT_ASKED)
    }

    fn shutdown(&mut self) {}
}

/// Each account's server, the connections opened to them, what each was asked, and the folders synced.
#[derive(Debug, Default)]
struct World {
    answers: Mutex<HashMap<String, Answers>>,
    opened: Mutex<Vec<String>>,
    asked: Arc<Mutex<Vec<String>>>,
    synced: Mutex<Vec<(String, String)>>,
    failing: Mutex<BTreeSet<String>>,
    accounts: Mutex<Vec<WarmAccount>>,
}

impl MailServers for World {
    fn open(&self, account: &str, _timeout_seconds: u64) -> Result<Box<dyn MailServer>, ServerError> {
        self.opened.lock().unwrap().push(account.to_owned());
        let answers = self.answers.lock().unwrap().get(account).cloned().unwrap();
        if matches!(answers, Answers::Unreachable) {
            return Err(ServerError::Unreachable("OSError"));
        }
        Ok(Box::new(Server { answers, asked: self.asked.clone() }))
    }
}

impl WarmSync for World {
    fn sync_recent(&self, account: &str, folder: &str, recent: usize) -> Result<usize, SyncError> {
        assert_eq!(recent, WARM_RECENT);
        self.synced.lock().unwrap().push((account.to_owned(), folder.to_owned()));
        if self.failing.lock().unwrap().contains(folder) {
            return Err(SyncError::Server(ServerError::Unreachable("abort")));
        }
        Ok(1)
    }
}

impl WarmAccounts for World {
    fn warm_accounts(&self) -> Result<Vec<WarmAccount>, Fault> {
        Ok(self.accounts.lock().unwrap().clone())
    }
}

fn account(email: &str, asks: bool, gmail: bool) -> WarmAccount {
    WarmAccount { email: email.to_owned(), asks, archive_is_label_removal: gmail }
}

/// Accounts asking to be warmed, each over a server answering as `answers` says, and the variable unset.
fn world(answers: &[(&str, Answers)]) -> Arc<World> {
    let world = World::default();
    for (email, said) in answers {
        world.answers.lock().unwrap().insert((*email).to_owned(), said.clone());
        world.accounts.lock().unwrap().push(account(email, true, false));
    }
    Arc::new(world)
}

fn warming(world: &Arc<World>, variable: Option<Which>) -> Warming {
    Warming::new(WarmingParts {
        accounts: world.clone(),
        servers: world.clone(),
        sync: world.clone(),
        variable,
        pause: Duration::ZERO,
    })
}

/// What each of `n` rounds synced, as (account, folder) by its account's name alone.
fn rounds(world: &World, warming: &Warming, n: usize, emails: Option<&BTreeSet<String>>) -> Vec<Vec<(String, String)>> {
    (0..n)
        .map(|_| {
            warming.tick(emails).unwrap();
            std::mem::take(&mut *world.synced.lock().unwrap())
                .into_iter()
                .map(|(account, folder)| (account.split('@').next().unwrap().to_owned(), folder))
                .collect()
        })
        .collect()
}

fn ada(folder: &str) -> (String, String) {
    ("ada".to_owned(), folder.to_owned())
}

fn bea(folder: &str) -> (String, String) {
    ("bea".to_owned(), folder.to_owned())
}

// Which accounts.

#[test]
fn the_variable_reads_as_every_account_none_or_the_addresses_it_lists() {
    let every = Which { every: true, listed: BTreeSet::new() };
    for on in ["1", "true", " YES ", "on", "All"] {
        assert_eq!(Which::read(Some(on)), Some(every.clone()), "{on}");
    }
    for off in ["0", "false", "No", "off"] {
        assert_eq!(Which::read(Some(off)), Some(Which::default()), "{off}");
    }
    assert_eq!(Which::read(None), None);
    assert_eq!(Which::read(Some("  ")), None);
    let listed = Which::read(Some(" ada@example.test, bea , ,Cy@Example.test")).unwrap();
    assert_eq!(listed, Which { every: false, listed: BTreeSet::from([ADA.to_owned(), "Cy@Example.test".to_owned()]) });
}

#[test]
fn the_variable_when_set_decides_over_the_file() {
    let world = world(&[(ADA, Answers::Lists(folders())), (BEA, Answers::Lists(folders()))]);
    world.accounts.lock().unwrap()[1].asks = false;
    // Unset: Ada's entry asks, Bea's doesn't.
    assert_eq!(rounds(&world, &warming(&world, None), 1, None), [vec![ada("Drafts")]]);
    // Every account, whatever the file says; then only those listed; then none.
    let every = Which { every: true, listed: BTreeSet::new() };
    assert_eq!(rounds(&world, &warming(&world, Some(every)), 1, None), [vec![ada("Drafts"), bea("Drafts")]]);
    let bea_only = Which::read(Some(BEA));
    assert_eq!(rounds(&world, &warming(&world, bea_only), 1, None), [vec![bea("Drafts")]]);
    let opened = world.opened.lock().unwrap().len();
    let none = warming(&world, Which::read(Some("0")));
    assert_eq!(rounds(&world, &none, 1, None), [Vec::new()]);
    assert_eq!(world.opened.lock().unwrap().len(), opened);
}

#[test]
fn with_no_account_warmed_a_round_asks_nothing_and_isnt_counted() {
    let world = world(&[(ADA, Answers::Lists(folders()))]);
    let warming = warming(&world, None);
    world.accounts.lock().unwrap()[0].asks = false;
    assert_eq!(rounds(&world, &warming, 2, None), [Vec::new(), Vec::new()]);
    assert!(world.opened.lock().unwrap().is_empty());
    // Asked for now, its first round is the first: the Drafts alone.
    world.accounts.lock().unwrap()[0].asks = true;
    assert_eq!(rounds(&world, &warming, 1, None), [vec![ada("Drafts")]]);
}

// Which folders.

#[test]
fn drafts_go_every_round_the_archive_every_second_and_sent_every_third() {
    let world = world(&[(ADA, Answers::Lists(folders()))]);
    assert_eq!(
        rounds(&world, &warming(&world, None), 6, None),
        [
            vec![ada("Drafts")],
            vec![ada("Drafts"), ada("All Mail")],
            vec![ada("Drafts"), ada("Sent Items")],
            vec![ada("Drafts"), ada("All Mail")],
            vec![ada("Drafts")],
            vec![ada("Drafts"), ada("All Mail"), ada("Sent Items")],
        ]
    );
}

#[test]
fn the_parts_are_found_over_one_connection_and_kept() {
    let world = world(&[(ADA, Answers::Lists(folders()))]);
    rounds(&world, &warming(&world, None), 3, None);
    assert_eq!(*world.opened.lock().unwrap(), [ADA]);
    assert_eq!(*world.asked.lock().unwrap(), ["LIST", "LIST", "LIST", "LOGOUT"]);
}

#[test]
fn a_gmail_accounts_all_mail_holds_its_inbox_so_it_isnt_warmed() {
    let world = world(&[(ADA, Answers::Lists(folders()))]);
    world.accounts.lock().unwrap()[0].archive_is_label_removal = true;
    assert_eq!(rounds(&world, &warming(&world, None), 6, None).last().unwrap(), &[ada("Drafts"), ada("Sent Items")]);
}

#[test]
fn a_part_no_folder_plays_or_the_inbox_plays_is_passed_over() {
    let world = world(&[(ADA, Answers::Lists(vec![folder("INBOX", "\\drafts"), folder("Sent", "\\sent")]))]);
    assert_eq!(rounds(&world, &warming(&world, None), 3, None), [Vec::new(), Vec::new(), vec![ada("Sent")]]);
}

#[test]
fn a_list_the_server_refuses_finds_no_folder_and_that_is_kept() {
    let world = world(&[(ADA, Answers::Refuses)]);
    assert_eq!(rounds(&world, &warming(&world, None), 6, None), vec![Vec::new(); 6]);
    assert_eq!(*world.opened.lock().unwrap(), [ADA]);
}

#[test]
fn an_account_that_cant_be_reached_is_tried_again_next_round_and_the_others_go_on() {
    let world = world(&[(ADA, Answers::Unreachable), (BEA, Answers::Lists(folders()))]);
    let warming = warming(&world, None);
    assert_eq!(rounds(&world, &warming, 2, None), [vec![bea("Drafts")], vec![bea("Drafts"), bea("All Mail")]]);
    assert_eq!(*world.opened.lock().unwrap(), [ADA, BEA, ADA]);
}

#[test]
fn a_connection_lost_while_the_folders_are_listed_is_tried_again_next_round() {
    let world = world(&[(ADA, Answers::LosesTheList)]);
    let warming = warming(&world, None);
    assert_eq!(rounds(&world, &warming, 1, None), [Vec::new()]);
    world.answers.lock().unwrap().insert(ADA.to_owned(), Answers::Lists(folders()));
    assert_eq!(rounds(&world, &warming, 1, None), [vec![ada("Drafts"), ada("All Mail")]]);
    assert_eq!(*world.opened.lock().unwrap(), [ADA, ADA]);
}

#[test]
fn a_folder_that_fails_leaves_the_rest_of_the_round_to_go() {
    let world = world(&[(ADA, Answers::Lists(folders())), (BEA, Answers::Lists(folders()))]);
    world.failing.lock().unwrap().insert("Drafts".to_owned());
    assert_eq!(
        rounds(&world, &warming(&world, None), 2, None)[1],
        [ada("Drafts"), ada("All Mail"), bea("Drafts"), bea("All Mail")]
    );
}

#[test]
fn a_round_for_some_accounts_warms_only_those() {
    let world = world(&[(ADA, Answers::Lists(folders())), (BEA, Answers::Lists(folders()))]);
    let named = BTreeSet::from([BEA.to_owned()]);
    assert_eq!(rounds(&world, &warming(&world, None), 1, Some(&named)), [vec![bea("Drafts")]]);
    assert_eq!(*world.opened.lock().unwrap(), [BEA]);
}
