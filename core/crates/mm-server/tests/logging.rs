//! The log as `mailmind-server` and the Mac app's host keep it (`logging`): a JSON object a line in `mailmind.log`,
//! with a UTC time, what the Python app's file holds and nothing more, readable by its owner only, and moved aside at
//! 1 MiB with three older files kept. Every address here is fictional.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use mm_kernel::log::{self, Field, Level, Line};
use mm_server::logging::{self, BACKUPS, FILE, LogFile, MAX_BYTES, WITHHELD};
use serde_json::{Value, json};

fn line<'a>(
    level: Level,
    message: &'static str,
    fields: &'a [(Field, &'a str)],
    error: Option<&'static str>,
) -> Line<'a> {
    Line { level, tag: "sync", message, fields, error }
}

fn lines(path: &Path) -> Vec<Value> {
    std::fs::read_to_string(path).unwrap().lines().map(|l| serde_json::from_str(l).unwrap()).collect()
}

#[cfg(unix)]
fn mode(path: &Path) -> u32 {
    std::os::unix::fs::PermissionsExt::mode(&std::fs::metadata(path).unwrap().permissions()) & 0o777
}

#[test]
fn a_value_holding_an_address_is_withheld_whole_and_the_rest_kept_to_one_short_line() {
    assert_eq!(logging::shown("ada@example.test"), WITHHELD);
    assert_eq!(logging::shown("mailto:ada%40example.test"), WITHHELD);
    assert_eq!(logging::shown("Timeout"), "Timeout");
    assert_eq!(logging::shown("two\nlines\there"), "two lines here");
    assert_eq!(logging::shown(&"x".repeat(1000)).len(), 300);
}

#[test]
fn the_time_is_utc_to_the_millisecond_as_python_writes_it() {
    let at = UNIX_EPOCH + Duration::from_millis(1_794_821_400_250);
    assert_eq!(logging::timestamp(at), "2026-11-16T09:30:00.250+00:00");
    assert_eq!(logging::timestamp(UNIX_EPOCH), "1970-01-01T00:00:00.000+00:00");
}

#[test]
fn a_line_in_the_file_is_one_json_object_with_what_pythons_holds() {
    let at = UNIX_EPOCH + Duration::from_millis(1_794_821_400_250);
    let fields = [(Field::Count, "3"), (Field::What, "ada@example.test")];
    let said = logging::json(&line(Level::Warning, "%(count)s failed: %(what)s", &fields, Some("Timeout")), at);
    let entry: Value = serde_json::from_str(&said).unwrap();
    assert_eq!(
        entry,
        json!({"ts": "2026-11-16T09:30:00.250+00:00", "level": "WARNING", "logger": "sync",
               "msg": "3 failed: <withheld>", "error": "Timeout", "count": "3", "what": WITHHELD})
    );
    let keys: Vec<&String> = entry.as_object().unwrap().keys().collect();
    assert_eq!(keys, ["ts", "level", "logger", "msg", "error", "count", "what"]);
    assert!(!said.contains('\n'));
    assert!(!said.contains("example.test"));
    // No error, no "error".
    let quiet: Value = serde_json::from_str(&logging::json(&line(Level::Info, "idle", &[], None), at)).unwrap();
    assert_eq!(quiet.as_object().unwrap().len(), 4);
}

#[test]
fn the_terminal_shows_the_level_the_tag_the_message_and_the_class() {
    let fields = [(Field::Job, "sync")];
    let said = logging::terminal(&line(Level::Error, "%(job)s failed", &fields, Some("Panic")));
    assert_eq!(said, "ERROR [sync] sync failed: Panic");
}

#[test]
#[cfg(unix)]
fn the_file_is_its_owners_alone_even_one_that_was_there() {
    let home = tempfile::tempdir().unwrap();
    let path = home.path().join(FILE);
    std::fs::write(&path, "").unwrap();
    std::fs::set_permissions(&path, std::os::unix::fs::PermissionsExt::from_mode(0o644)).unwrap();
    let mut file = LogFile::at(path.clone());
    file.write("{}");
    assert_eq!(mode(&path), 0o600);
    assert_eq!(std::fs::read_to_string(&path).unwrap(), "{}\n");
}

#[test]
#[cfg(unix)]
fn at_a_mebibyte_the_file_moves_aside_and_three_older_ones_are_kept() {
    let home = tempfile::tempdir().unwrap();
    let path = home.path().join(FILE);
    let mut file = LogFile::at(path.clone());
    let text = "y".repeat(1023);
    // Each line is 1 KiB with its newline: 1023 fill the file short of 1 MiB, and the 1024th moves it aside.
    for _ in 0..1023 {
        file.write(&text);
    }
    assert_eq!(std::fs::metadata(&path).unwrap().len(), MAX_BYTES - 1024);
    assert!(!home.path().join("mailmind.log.1").exists());
    file.write(&text);
    assert_eq!(std::fs::metadata(&path).unwrap().len(), 1024);
    assert_eq!(std::fs::metadata(home.path().join("mailmind.log.1")).unwrap().len(), MAX_BYTES - 1024);
    // Four more moves: the oldest goes, and no fifth file is made.
    for round in 0..4 {
        for _ in 0..1023 {
            file.write(&format!("{round}{}", &text[1..]));
        }
    }
    let mut kept: Vec<String> =
        std::fs::read_dir(home.path()).unwrap().map(|e| e.unwrap().file_name().into_string().unwrap()).collect();
    kept.sort();
    assert_eq!(kept, ["mailmind.log", "mailmind.log.1", "mailmind.log.2", "mailmind.log.3"]);
    assert_eq!(BACKUPS, 3);
    for name in &kept {
        assert_eq!(mode(&home.path().join(name)), 0o600, "{name}");
        assert!(std::fs::metadata(home.path().join(name)).unwrap().len() <= MAX_BYTES, "{name}");
    }
    // The newest moved file holds the last full round's lines.
    let newest = std::fs::read_to_string(home.path().join("mailmind.log.1")).unwrap();
    assert!(newest.lines().all(|l| l.starts_with('2') || l.starts_with('3')), "{}", &newest[..8]);
}

#[test]
fn a_line_that_cant_be_written_is_lost_and_the_next_goes_once_it_can() {
    let home = tempfile::tempdir().unwrap();
    let folder = home.path().join("not-yet");
    let mut file = LogFile::at(folder.join(FILE));
    file.write("lost");
    std::fs::create_dir(&folder).unwrap();
    file.write("kept");
    assert_eq!(std::fs::read_to_string(folder.join(FILE)).unwrap(), "kept\n");
}

/// The one test here that installs the process's log, so no other sees its lines.
#[test]
fn once_a_data_folder_is_named_every_line_but_debugs_goes_to_its_file_too() {
    let home = tempfile::tempdir().unwrap();
    logging::keep_in(home.path(), false);
    let before = SystemTime::now();
    log::warning("jobs", "%(job)s failed", &[(Field::Job, "sync")], Some("Timeout"));
    log::line(&Line { level: Level::Debug, tag: "jobs", message: "a debug line", fields: &[], error: None });
    log::info("sync", "%(what)s synced", &[(Field::What, "ada@example.test")]);
    let written = lines(&home.path().join(FILE));
    assert_eq!(written.len(), 2);
    assert_eq!(
        (&written[0]["level"], &written[0]["logger"], &written[0]["msg"], &written[0]["error"], &written[0]["job"]),
        (&json!("WARNING"), &json!("jobs"), &json!("sync failed"), &json!("Timeout"), &json!("sync"))
    );
    assert_eq!(written[1]["msg"], json!("<withheld> synced"));
    let stamp = written[0]["ts"].as_str().unwrap();
    assert!(stamp.ends_with("+00:00") && stamp[..10] == logging::timestamp(before)[..10], "{stamp}");
    #[cfg(unix)]
    assert_eq!(mode(&home.path().join(FILE)), 0o600);
    // Named again elsewhere, the file moves there.
    let other = tempfile::tempdir().unwrap();
    logging::keep_in(other.path(), false);
    log::info("sync", "%(count)s new", &[(Field::Count, "0")]);
    assert_eq!(lines(&home.path().join(FILE)).len(), 2);
    assert_eq!(lines(&other.path().join(FILE))[0]["msg"], json!("0 new"));
}
