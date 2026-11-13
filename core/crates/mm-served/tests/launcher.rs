//! What the launcher hands the server: the frozen clock, the tripwire guard.py is for the Python app, and the fake
//! Keychain's file.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::Arc;

use mm_kernel::seams::{Clock, Keychain, Moment, Outside, Tripped};
use mm_served::clock::{FrozenClock, parse_iso};
use mm_served::keychain::FakeKeychain;
use mm_served::tripwire::Tripwire;

const NOW: i64 = 1_793_620_800;

#[test]
fn the_clock_reads_the_times_pythons_isoformat_writes() {
    let at = |text| parse_iso(text).map(Moment::micros);
    assert_eq!(at("2026-11-02T12:00:00+00:00"), Some(NOW * 1_000_000));
    assert_eq!(at("2026-11-02T12:00:00"), Some(NOW * 1_000_000), "one without an offset is UTC");
    assert_eq!(at("2026-11-02 12:00:00Z"), Some(NOW * 1_000_000));
    assert_eq!(at("2026-11-02T13:30:00.25+01:30"), Some(NOW * 1_000_000 + 250_000));
    assert_eq!(at("2026-11-02T07:00:00-05:00"), Some(NOW * 1_000_000));
    assert_eq!(at("2026-11-02"), Some((NOW - 12 * 3600) * 1_000_000));
    for wrong in ["", "soon", "2026-13-02T12:00:00", "2026-11-02T25:00:00", "2026-11-02T12:00:00.1234567"] {
        assert_eq!(at(wrong), None, "{wrong:?}");
    }
    let clock = FrozenClock::at(Moment::from_micros(NOW * 1_000_000));
    assert_eq!(clock.now(), clock.now());
    clock.move_to(Moment::from_micros(0));
    assert_eq!(clock.now(), Moment::from_micros(0));
}

fn refused(result: Result<(), Tripped>) -> bool {
    result.is_err()
}

#[test]
fn the_owners_data_and_anything_else_under_a_guarded_folder_is_refused() {
    let root = tempfile::tempdir().unwrap();
    let (repo, guarded, outside) = (root.path().join("repo"), root.path().join("home"), root.path().join("tmp"));
    let (data, dist) = (repo.join("data"), outside.join("dist"));
    for folder in [&repo, &guarded, &data, &dist] {
        std::fs::create_dir_all(folder).unwrap();
    }
    let tripwire = Tripwire::new(&repo, std::slice::from_ref(&guarded), &[data.clone(), dist.clone()]);

    for private in ["mailmind.db", "MAILMIND.DB-wal", "mailmind.config.json.bak", "mailmind.log.1", ".cfg-1-2.tmp"] {
        assert!(refused(tripwire.open(&guarded.join(private))), "{private}");
    }
    assert!(refused(tripwire.open(&repo.join("mailmind.db"))));
    assert!(refused(tripwire.open(&repo.join("notes.txt"))), "nothing under the repo root but the data and the page");
    assert!(refused(tripwire.open(&repo.join("sub/../mailmind.db"))));
    assert!(!refused(tripwire.open(&data.join("mailmind.db"))));
    assert!(!refused(tripwire.open(&dist.join("index.html"))));
    assert!(!refused(tripwire.open(&outside.join("keychain.json"))));
    #[cfg(unix)]
    {
        let link = outside.join("innocent.txt");
        std::os::unix::fs::symlink(guarded.join("mailmind.db"), &link).unwrap();
        std::fs::write(guarded.join("mailmind.db"), "").unwrap();
        assert!(refused(tripwire.open(&link)), "a link is judged by where it leads");
    }
    let taken = tripwire.take();
    assert!(taken.len() >= 8 && taken.iter().all(|t| t.starts_with("open ")));
    assert!(tripwire.take().is_empty(), "what it refused is reported once");
}

#[test]
fn nothing_past_this_machine_is_reached_and_no_credential_helper_runs() {
    let root = tempfile::tempdir().unwrap();
    let tripwire = Tripwire::new(root.path(), &[], &[]);
    for host in ["127.0.0.1", "localhost", "::1", "::ffff:127.0.0.1", "127.8.9.10", "", " LOCALHOST "] {
        assert!(!refused(tripwire.reach(host)), "{host:?}");
    }
    for host in ["imap.example.test", "10.0.0.1", "::ffff:10.0.0.1", "8.8.8.8"] {
        assert!(refused(tripwire.reach(host)), "{host:?}");
    }
    for program in ["claude", "/usr/bin/security", "op"] {
        assert!(refused(tripwire.run(program)), "{program}");
    }
    assert!(!refused(tripwire.run("/bin/ls")));
    assert_eq!(
        tripwire.take()[..2],
        ["DNS lookup of \"imap.example.test\"".to_owned(), "connect to \"10.0.0.1\"".to_owned()]
    );
}

#[derive(Debug)]
struct Open;

impl Outside for Open {
    fn open(&self, _path: &Path) -> Result<(), Tripped> {
        Ok(())
    }

    fn reach(&self, _host: &str) -> Result<(), Tripped> {
        Ok(())
    }

    fn run(&self, _program: &str) -> Result<(), Tripped> {
        Ok(())
    }
}

#[test]
fn the_keychain_is_the_file_the_suite_shares() {
    let root = tempfile::tempdir().unwrap();
    let file = root.path().join("keychain.json");
    let keychain = FakeKeychain::new(Some(file.clone()), Arc::new(Open));
    assert!(keychain.find("account:ada@example.test").unwrap().is_none(), "no file yet holds nothing");
    std::fs::write(&file, r#"{"account:ada@example.test": "app-password-0"}"#).unwrap();
    assert_eq!(keychain.find("account:ada@example.test").unwrap().unwrap().expose(), "app-password-0");
    assert!(keychain.find("account:bea@example.test").unwrap().is_none());
    std::fs::write(&file, "not json").unwrap();
    assert!(keychain.find("account:ada@example.test").is_err());
    assert!(FakeKeychain::new(None, Arc::new(Open)).find("anything").unwrap().is_none());
}
