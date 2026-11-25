//! A test's home in a test world: with a keychain stand-in and `MAILMIND_TEST_CLOCK=paused`, the app reads a clock
//! that stands until the test moves it and runs no job until the test pumps it. Any other home runs on the system's
//! clock with its jobs running by themselves, and a home with no stand-in (a person's) is never asked.
//!
//! Every home is a temporary folder with fictional data, its database keyed by a key made for the test and its
//! Keychain a file: nothing here reads the login Keychain or reaches a host off this machine.

use std::path::Path;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use mm_host::{Home, Host};
use mm_kernel::seams::Moment;
use mm_store::DbKey;
use mm_store::db::Database;

/// A key made for the tests: it guards only fictional mail.
const KEY: &str = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0";
/// 2026-11-02 12:00:00 UTC, where the test clock stands as the app opens.
const NOW: i64 = 1_793_620_800;
const NOW_ISO: &str = "2026-11-02T12:00:00+00:00";

fn moment(seconds: i64) -> Moment {
    Moment::from_micros(seconds * 1_000_000)
}

/// A test's home: the key, the keychain's stand-in, and `variables` besides.
fn home(folder: &Path, variables: &[(&str, &str)]) -> Home {
    let mut given = vec![("MAILMIND_DB_KEY".to_owned(), KEY.to_owned())];
    given.extend(variables.iter().map(|(name, value)| ((*name).to_owned(), (*value).to_owned())));
    Home { folder: folder.to_path_buf(), variables: given, stand_in: Some(folder.join("keychain.json")) }
}

const PAUSED: [(&str, &str); 2] = [("MAILMIND_TEST_CLOCK", "paused"), ("MAILMIND_TEST_NOW", NOW_ISO)];

fn paused(folder: &Path) -> Host {
    Host::open(home(folder, &PAUSED)).expect("the host opens a fictional home")
}

/// The home's database, as the test reaches it beside the app.
fn database(folder: &Path) -> Database {
    let key = DbKey::from_hex(KEY).expect("a key");
    Database::new(folder.join("mailmind.db"), key, Arc::new(mm_host::stand_in::LoopbackOnly))
}

fn snoozed(folder: &Path) -> i64 {
    database(folder).read(|c| c.query_row("SELECT count(*) FROM snoozes", [], |r| r.get(0))).expect("counted")
}

#[test]
fn a_test_home_that_asks_reads_a_clock_that_stands_until_the_test_moves_it() {
    let folder = tempfile::tempdir().expect("a home");
    let host = paused(folder.path());
    let world = host.test_world().expect("a test world");
    assert_eq!(host.app().clock.now(), moment(NOW), "it stands where MAILMIND_TEST_NOW says");
    std::thread::sleep(Duration::from_millis(30));
    assert_eq!(host.app().clock.now(), moment(NOW), "and stays there");
    assert_eq!(world.advance(90 * 1_000_000), moment(NOW + 90));
    assert_eq!(host.app().clock.now(), moment(NOW + 90), "the app reads where the test moved it");
    world.set(moment(NOW - 3600));
    assert_eq!(world.now(), moment(NOW - 3600));
    assert!(host.app().jobs.names().is_empty(), "no worker started: {:?}", host.app().jobs.names());

    // What the app kicks waits for the test: a wake kicks the sync and the older mail, which a pump lets go.
    host.woke();
    world.pump_kicked().expect("a pass of each kicked job");
    assert!(host.app().jobs.kicked().is_empty(), "each kicked job was pumped");
    assert_eq!(
        world.pump("nothing-of-the-kind", &[]),
        Err("failed nothing-of-the-kind: not on the Rust server yet".into())
    );
    host.close();
    assert!(host.test_world().is_none(), "nothing to move once it has closed");
}

#[test]
fn moving_the_clock_past_a_snooze_brings_it_due_and_a_pump_wakes_it() {
    let folder = tempfile::tempdir().expect("a home");
    let host = paused(folder.path());
    let world = host.test_world().expect("a test world");
    // A fictional message in the Inbox, snoozed for an hour.
    database(folder.path())
        .write(|c| {
            c.execute_batch(&format!(
                "INSERT INTO message_identity(account, identity) VALUES ('ada@example.test', '<venue@example.test>');
                 INSERT INTO messages(account, kind, folder, uid, message_id, thread_id, subject, ts, seen, message_key)
                   VALUES ('ada@example.test', 'imap', 'INBOX', 1, '<venue@example.test>', 't1', 'Venue', {NOW}, 0,
                           last_insert_rowid());
                 INSERT INTO snoozes(message_key, folder, until)
                   SELECT message_key, 'INBOX', {} FROM messages WHERE uid = 1;",
                NOW + 3600
            ))
        })
        .expect("seeded");
    assert_eq!(snoozed(folder.path()), 1);

    std::thread::sleep(Duration::from_millis(50));
    world.pump("snoozes", &[]).expect("a pass of the snoozes");
    assert_eq!(snoozed(folder.path()), 1, "an hour hasn't passed on the test clock, however long the test takes");
    let before = world.high_water();

    world.advance(2 * 3600 * 1_000_000);
    assert_eq!(snoozed(folder.path()), 1, "moving the clock runs nothing by itself");
    world.pump("snoozes", &[]).expect("a pass of the snoozes");
    assert_eq!(snoozed(folder.path()), 0, "the snooze that came due woke");
    assert_ne!(world.high_water(), before, "and the app said so on the event stream");
}

#[test]
fn settle_returns_once_the_work_a_request_set_going_is_done() {
    let folder = tempfile::tempdir().expect("a home");
    let host = paused(folder.path());
    let world = host.test_world().expect("a test world");
    assert_eq!(world.settle(Duration::from_secs(1)), 0, "nothing is being answered");

    let (go, wait) = std::sync::mpsc::channel::<()>();
    let done = Arc::new(AtomicBool::new(false));
    let finished = done.clone();
    host.app().work.spawn(move || {
        let _ = wait.recv_timeout(Duration::from_secs(10));
        finished.store(true, Ordering::SeqCst);
    });
    assert_eq!(world.settle(Duration::from_millis(50)), 1, "still busy when its time runs out");
    let _ = go.send(());
    assert_eq!(world.settle(Duration::from_secs(10)), 0);
    assert!(done.load(Ordering::SeqCst), "settled only once the work was done");
}

#[test]
fn without_the_variable_a_test_home_runs_on_the_systems_clock_and_its_jobs_run_free() {
    let folder = tempfile::tempdir().expect("a home");
    let host = Host::open(home(folder.path(), &[("MAILMIND_TEST_NOW", NOW_ISO)])).expect("opened");
    assert!(host.test_world().is_none());
    let system = SystemTime::now().duration_since(UNIX_EPOCH).expect("after 1970").as_secs_f64();
    let read = host.app().clock.now().seconds();
    assert!((read - system).abs() < 60.0, "the system's clock: {read} against {system}");
    assert!(host.app().jobs.names().contains(&"snooze"), "the workers started: {:?}", host.app().jobs.names());
    host.close();

    let empty = Host::open(home(folder.path(), &[("MAILMIND_TEST_CLOCK", "")])).expect("opened");
    assert!(empty.test_world().is_none(), "an empty value is unset");
    empty.close();
}

#[test]
fn a_test_home_that_names_another_clock_or_no_time_does_not_open() {
    let folder = tempfile::tempdir().expect("a home");
    let refused = Host::open(home(folder.path(), &[("MAILMIND_TEST_CLOCK", "running")])).expect_err("refused");
    assert_eq!(refused, "MAILMIND_TEST_CLOCK is paused or unset");
    let at = [("MAILMIND_TEST_CLOCK", "paused"), ("MAILMIND_TEST_NOW", "soon")];
    assert_eq!(Host::open(home(folder.path(), &at)).expect_err("refused"), "MAILMIND_TEST_NOW isn't a time");
    let later = Host::open(home(folder.path(), &[("MAILMIND_TEST_CLOCK", " Paused ")])).expect("opened");
    let read = later.test_world().expect("a test world").now().seconds();
    let system = SystemTime::now().duration_since(UNIX_EPOCH).expect("after 1970").as_secs_f64();
    assert!((read - system).abs() < 60.0, "with no MAILMIND_TEST_NOW it stands where the system's clock stood");
}

#[test]
fn a_home_with_no_stand_in_never_gets_the_test_clock() {
    // A person's home has no stand-in. It isn't opened here, as it would reach the login Keychain: what it would get is
    // the question `open` asks first.
    let folder = tempfile::tempdir().expect("a home");
    for clock in ["paused", "running"] {
        let mut person = home(folder.path(), &[("MAILMIND_TEST_CLOCK", clock), ("MAILMIND_TEST_NOW", NOW_ISO)]);
        person.stand_in = None;
        assert_eq!(person.test_clock(), Ok(None), "{clock}: its variables aren't read");
    }
    assert_eq!(home(folder.path(), &PAUSED).test_clock(), Ok(Some(moment(NOW))), "a test's home that asks gets it");
    assert_eq!(home(folder.path(), &[]).test_clock(), Ok(None), "and one that doesn't, doesn't");
}
