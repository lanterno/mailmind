//! The kernel's refusals, its moments and its text, as the Python app's answers need them.

#![allow(clippy::unwrap_used)]

use std::sync::{Arc, Mutex};

use mm_kernel::errors::{Failure, Fault, Kind, Problem};
use mm_kernel::log::{self, Field, Line, Sink};
use mm_kernel::seams::{Moment, Secret};
use mm_kernel::text::{is_printable, is_space, strip};
use mm_kernel::when::iso;
use serde_json::json;

/// 2026-11-02 12:00 UTC, the suites' frozen now.
const NOW: i64 = 1_793_620_800;

#[test]
fn a_busy_problem_says_so_beside_its_words() {
    let busy = Problem::new(Kind::Busy, "another job holds the account");
    assert_eq!(busy.more, vec![("busy", json!(true))]);
    assert!(Problem::new(Kind::Refused, "no").more.is_empty());
}

#[test]
fn a_malformed_request_says_only_bad_request() {
    assert_eq!(Problem::bad_request(), Problem::refused("bad request"));
    assert_eq!(Problem::not_found("gone").with("id", json!(7)).more, vec![("id", json!(7))]);
}

#[test]
fn a_fault_says_its_class_and_nothing_else() {
    assert_eq!(Failure::from(Fault::new("Timeout")).to_string(), "Timeout");
    assert_eq!(Failure::from(Problem::refused("unknown account")).to_string(), "Refused: unknown account");
}

#[test]
fn a_moment_reads_as_pythons_isoformat_writes_it() {
    assert_eq!(iso(Moment::from_micros(NOW * 1_000_000)), "2026-11-02T12:00:00+00:00");
    assert_eq!(iso(Moment::from_micros(NOW * 1_000_000 + 250_000)), "2026-11-02T12:00:00.250000+00:00");
    assert_eq!(iso(Moment::from_micros(0)), "1970-01-01T00:00:00+00:00");
    assert_eq!(Moment::from_micros(NOW * 1_000_000 + 999_999).whole_seconds(), NOW);
    assert_eq!(Moment::from_micros(-1).whole_seconds(), -1);
}

#[test]
fn a_secret_never_prints() {
    let secret = Secret::new("app-password-0".to_owned());
    assert_eq!(format!("{secret:?}"), "Secret(..)");
    assert_eq!(secret.expose(), "app-password-0");
}

#[test]
fn text_is_spaced_and_printable_as_python_says() {
    for c in [' ', '\t', '\n', '\u{1c}', '\u{1f}', '\u{85}', '\u{a0}', '\u{3000}'] {
        assert!(is_space(c), "{c:?}");
    }
    assert_eq!(strip("\u{1c} ada \u{a0}"), "ada");
    assert!(is_printable('é') && is_printable(' ') && is_printable('字'));
    for c in ['\u{0}', '\n', '\u{200b}', '\u{202e}', '\u{e000}', '\u{a0}'] {
        assert!(!is_printable(c), "{c:?}");
    }
}

#[derive(Debug, Default)]
struct Kept(Mutex<Vec<String>>);

#[derive(Debug)]
struct Keeping(Arc<Kept>);

impl Sink for Keeping {
    fn write(&self, line: &Line<'_>) {
        let fields: Vec<String> = line.fields.iter().map(|(f, v)| format!("{}={v}", f.name())).collect();
        let said = format!("{} [{}] {} {:?} {:?}", line.level.name(), line.tag, line.message, fields, line.error);
        self.0.0.lock().unwrap().push(said);
    }
}

#[test]
fn a_line_goes_to_the_sink_the_program_installed() {
    let kept = Arc::new(Kept::default());
    log::install(Box::new(Keeping(kept.clone())));
    log::error(
        "http",
        "%(method)s %(path)s failed",
        &[(Field::Method, "GET"), (Field::Path, "/api/folders")],
        Some("Timeout"),
    );
    assert_eq!(
        kept.0.lock().unwrap().as_slice(),
        [r#"ERROR [http] %(method)s %(path)s failed ["method=GET", "path=/api/folders"] Some("Timeout")"#]
    );
}
