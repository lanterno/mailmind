//! The message list as a shell meets it: a window in, batches of the rows it lacks out.

#![allow(clippy::unwrap_used)]

use std::sync::{Arc, Condvar, Mutex};
use std::time::Duration;

use mm_app::{DbKey, ListQuery, ListView, Refusal, Session, ViewBatch, ViewObserver};
use mm_fixtures::{ACCOUNTS, FIXTURE_KEY_HEX, FOLDER};
use mm_store::Store;

#[derive(Default)]
struct Shell {
    batches: Mutex<Vec<ViewBatch>>,
    refusals: Mutex<Vec<Refusal>>,
    arrived: Condvar,
}

impl ViewObserver for Shell {
    fn on_batch(&self, batch: ViewBatch) {
        self.batches.lock().unwrap().push(batch);
        self.arrived.notify_all();
    }

    fn on_refusal(&self, refusal: Refusal) {
        self.refusals.lock().unwrap().push(refusal);
        self.arrived.notify_all();
    }
}

impl Shell {
    fn wait_for(&self, batches: usize) -> Vec<ViewBatch> {
        let held = self.batches.lock().unwrap();
        let (held, timeout) =
            self.arrived.wait_timeout_while(held, Duration::from_secs(5), |b| b.len() < batches).unwrap();
        assert!(!timeout.timed_out(), "only {} of {batches} batches arrived", held.len());
        held.clone()
    }
}

fn session(messages: u32, workers: u8) -> Session {
    let key = DbKey::from_hex(FIXTURE_KEY_HEX).unwrap();
    let mut store = Store::open_in_memory(&key).unwrap();
    store.create_list_tables().unwrap();
    store.add_messages(mm_fixtures::messages(messages, 7)).unwrap();
    Session::with_store(store, workers).unwrap()
}

fn inbox() -> ListQuery {
    ListQuery { accounts: ACCOUNTS.iter().map(|a| (*a).to_owned()).collect(), folder: FOLDER.to_owned() }
}

fn open(session: &Session) -> (Arc<ListView>, Arc<Shell>) {
    let shell = Arc::new(Shell::default());
    let list = session.open_list(inbox(), shell.clone()).unwrap();
    (list, shell)
}

#[test]
fn opening_a_list_loads_nothing_until_the_shell_says_what_is_visible() {
    let session = session(300, 0);
    let (list, shell) = open(&session);
    assert_eq!(list.total(), 300);
    assert!(shell.batches.lock().unwrap().is_empty());
}

#[test]
fn the_first_window_brings_its_rows_and_a_page_after_them() {
    let session = session(300, 0);
    let (list, shell) = open(&session);
    list.set_visible(0, 20);
    let batches = shell.wait_for(1);
    let batch = &batches[0];
    assert_eq!((batch.total, batch.held_first, batch.held_count), (300, 0, 100));
    assert_eq!(batch.ranges.len(), 1);
    assert_eq!(batch.ranges[0].first, 0);
    assert_eq!(batch.ranges[0].rows.len(), 100);
}

#[test]
fn rows_come_newest_first() {
    let session = session(300, 0);
    let (list, shell) = open(&session);
    list.set_visible(0, 20);
    let rows = &shell.wait_for(1)[0].ranges[0].rows;
    assert!(rows.windows(2).all(|pair| pair[0].date >= pair[1].date));
}

#[test]
fn a_scroll_inside_the_held_pages_sends_nothing() {
    let session = session(300, 0);
    let (list, shell) = open(&session);
    list.set_visible(0, 20);
    list.set_visible(10, 20);
    list.set_visible(29, 20);
    assert_eq!(shell.batches.lock().unwrap().len(), 1);
}

#[test]
fn crossing_a_page_sends_only_the_new_page() {
    let session = session(300, 0);
    let (list, shell) = open(&session);
    list.set_visible(0, 20);
    list.set_visible(50, 20);
    let batches = shell.wait_for(2);
    let batch = &batches[1];
    assert_eq!((batch.held_first, batch.held_count), (0, 150));
    assert_eq!(batch.ranges.len(), 1);
    assert_eq!((batch.ranges[0].first, batch.ranges[0].rows.len()), (100, 50));
}

#[test]
fn a_jump_sends_the_whole_new_window() {
    let session = session(1000, 0);
    let (list, shell) = open(&session);
    list.set_visible(0, 20);
    list.set_visible(700, 20);
    let batch = &shell.wait_for(2)[1];
    assert_eq!((batch.held_first, batch.held_count), (650, 150));
    assert_eq!(batch.ranges.len(), 1);
    assert_eq!((batch.ranges[0].first, batch.ranges[0].rows.len()), (650, 150));
}

#[test]
fn the_same_row_has_the_same_key_however_it_is_reached() {
    let session = session(1000, 0);
    let (list, shell) = open(&session);
    list.set_visible(0, 20);
    list.set_visible(700, 20);
    list.set_visible(100, 20);
    let batches = shell.wait_for(3);
    let at_60 = |batch: &ViewBatch| {
        batch.ranges.iter().find_map(|r| r.rows.get(60usize.checked_sub(r.first as usize)?).map(|row| row.key))
    };
    assert!(at_60(&batches[0]).is_some());
    assert_eq!(at_60(&batches[0]), at_60(&batches[2]));
}

#[test]
fn an_empty_list_still_tells_the_shell_it_is_empty() {
    let session = session(0, 0);
    let (list, shell) = open(&session);
    list.set_visible(0, 20);
    let batch = &shell.wait_for(1)[0];
    assert_eq!((batch.total, batch.held_count), (0, 0));
    assert!(batch.ranges.is_empty());
}

#[test]
fn a_list_of_no_accounts_is_empty() {
    let session = session(100, 0);
    let shell = Arc::new(Shell::default());
    let list = session.open_list(ListQuery { accounts: vec![], folder: FOLDER.to_owned() }, shell).unwrap();
    assert_eq!(list.total(), 0);
}

#[test]
fn a_view_on_its_own_thread_answers_too() {
    let session = session(300, 1);
    let (list, shell) = open(&session);
    list.set_visible(0, 20);
    assert_eq!(shell.wait_for(1)[0].held_count, 100);
}

#[test]
fn windows_asked_faster_than_served_end_with_the_last_one_held() {
    let session = session(5000, 1);
    let (list, shell) = open(&session);
    for first in (0..4000).step_by(7) {
        list.set_visible(first, 20);
    }
    list.set_visible(4000, 20);
    let deadline = std::time::Instant::now() + Duration::from_secs(5);
    loop {
        let last = shell.batches.lock().unwrap().last().map(|b| (b.held_first, b.held_count));
        if last == Some((3950, 150)) {
            break;
        }
        assert!(std::time::Instant::now() < deadline, "the last window was not served: {last:?}");
        std::thread::sleep(Duration::from_millis(5));
    }
    assert!(shell.batches.lock().unwrap().len() <= 572, "every call was served, none collapsed");
}

#[test]
fn a_dropped_view_is_never_served() {
    let session = session(300, 1);
    let (list, shell) = open(&session);
    drop(list);
    std::thread::sleep(Duration::from_millis(50));
    assert!(shell.batches.lock().unwrap().is_empty());
}

#[test]
fn the_sender_is_the_name_when_there_is_one() {
    let session = session(50, 0);
    let (list, shell) = open(&session);
    list.set_visible(0, 20);
    let rows = &shell.wait_for(1)[0].ranges[0].rows;
    assert!(rows.iter().all(|row| !row.sender.contains('@') && row.sender.contains(' ')));
}

#[test]
fn a_wrong_key_opens_nothing() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mail.db");
    mm_fixtures::build(&path, &DbKey::from_hex(FIXTURE_KEY_HEX).unwrap(), 10, 7).unwrap();
    let other = DbKey::from_bytes([7; 32]);
    let opened = Session::open(mm_app::SessionConfig { database: path, key: other, workers: 0 });
    assert_eq!(opened.err(), Some(Refusal::KeyDoesNotFit));
}
