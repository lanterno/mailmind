//! `CoreHost`'s test world as the Mac driver will reach it: the clock set and moved in ISO 8601 and seconds, jobs
//! kicked and pumped, settling and the high-water id; and a refusal from a host that doesn't run in one.
//!
//! The homes are temporary folders with no mail, keyed by a key made for the test, their Keychain a file. Opening a
//! host installs its panic hook, which logs where a panic happened and not what it said: an assertion here that fails
//! says only its line.

#![cfg(feature = "host")]

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

use mailmind_core::{CoreHome, CoreHost, TestWorldProblem};

/// A key made for the tests: it guards only fictional mail.
const KEY: &str = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0";

/// A temporary folder of the test's own, removed as it drops.
struct Folder(PathBuf);

impl Folder {
    fn new(name: &str) -> Folder {
        let path = std::env::temp_dir().join(format!("mm-ffi-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&path);
        std::fs::create_dir_all(&path).expect("a home");
        Folder(path)
    }
}

impl Drop for Folder {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn opened(folder: &Folder, variables: &[(&str, &str)]) -> Arc<CoreHost> {
    let mut given: HashMap<String, String> = HashMap::from([("MAILMIND_DB_KEY".to_owned(), KEY.to_owned())]);
    given.extend(variables.iter().map(|(name, value)| ((*name).to_owned(), (*value).to_owned())));
    let home = CoreHome {
        folder: folder.0.to_string_lossy().into_owned(),
        variables: given,
        stand_in: Some(folder.0.join("keychain.json").to_string_lossy().into_owned()),
    };
    CoreHost::open(home).expect("the host opens a fictional home")
}

fn failed(why: &str) -> TestWorldProblem {
    TestWorldProblem::Failed { why: why.to_owned() }
}

#[test]
fn a_test_worlds_clock_and_jobs_are_moved_through_core_host() {
    let folder = Folder::new("test-world");
    let host = opened(&folder, &[("MAILMIND_TEST_CLOCK", "paused"), ("MAILMIND_TEST_NOW", "2026-11-02T12:00:00Z")]);
    assert!(host.in_test_world());
    assert_eq!(host.test_now(), Ok("2026-11-02T12:00:00+00:00".to_owned()));
    assert_eq!(host.test_advance_clock(90.5), Ok("2026-11-02T12:01:30.500000+00:00".to_owned()));
    assert_eq!(host.test_advance_clock(f64::NAN), Err(failed("not a span of time")));
    assert_eq!(host.test_set_clock("2026-11-03T08:00:00+01:00".to_owned()), Ok(()));
    assert_eq!(host.test_now(), Ok("2026-11-03T07:00:00+00:00".to_owned()));
    assert_eq!(host.test_set_clock("soon".to_owned()), Err(failed("not a time")));

    assert_eq!(host.test_kick("snoozes".to_owned()), Ok(()));
    assert_eq!(host.test_pump_kicked(), Ok(()));
    assert_eq!(host.test_pump("snoozes".to_owned(), Vec::new()), Ok(()));
    let unknown = host.test_pump("nothing-of-the-kind".to_owned(), Vec::new());
    assert_eq!(unknown, Err(failed("failed nothing-of-the-kind: not on the Rust server yet")));
    assert_eq!(host.test_settle(5.0), Ok(0));
    let id = host.test_high_water().expect("the high-water id");
    assert!(id.rsplit_once('-').is_some_and(|(_, n)| n.parse::<u64>().is_ok()), "<run>-<n>: {id}");

    host.close();
    assert!(!host.in_test_world());
    assert_eq!(host.test_now(), Err(TestWorldProblem::NotATestWorld), "nothing to move once it has closed");
}

#[test]
fn a_host_out_of_a_test_world_refuses_the_tests_controls() {
    let folder = Folder::new("free");
    let host = opened(&folder, &[]);
    assert!(!host.in_test_world());
    assert_eq!(host.test_now(), Err(TestWorldProblem::NotATestWorld));
    assert_eq!(host.test_advance_clock(60.0), Err(TestWorldProblem::NotATestWorld));
    assert_eq!(host.test_pump("snoozes".to_owned(), Vec::new()), Err(TestWorldProblem::NotATestWorld));
    assert_eq!(host.test_settle(1.0), Err(TestWorldProblem::NotATestWorld));
    host.close();
}
