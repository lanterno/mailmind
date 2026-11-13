//! The page's places: which ids the page makes, how a route is kept, and what opening one reads back.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::Arc;

use mm_kernel::errors::{Failure, Kind};
use mm_kernel::seams::{Clock, Moment, Outside, Tripped};
use mm_store::places::{self, Places};
use mm_store::{Database, DbKey, Store};
use serde_json::json;

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

#[derive(Debug)]
struct Frozen;

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000)
    }
}

const ID: &str = "AAAAAAAAAAAAAAAAAAAAAA";

fn places() -> (tempfile::TempDir, Places, Arc<Database>) {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    let key = DbKey::from_bytes([0x5e; 32]);
    Store::open(&path, &key).unwrap();
    let database = Arc::new(Database::new(path, key, Arc::new(Open)));
    database
        .read(|c| c.execute_batch("CREATE TABLE places (id TEXT PRIMARY KEY, route TEXT NOT NULL, used_at TEXT)"))
        .unwrap();
    (dir, Places::new(database.clone(), Arc::new(Frozen)), database)
}

fn refused(failure: Failure) -> String {
    match failure {
        Failure::Problem(problem) if problem.kind == Kind::Refused => problem.said,
        other => panic!("{other:?}"),
    }
}

#[test]
fn an_id_is_what_the_page_makes() {
    assert!(places::is_id(ID) && places::is_id("abc-DEF_0123456789") && places::is_id(&"a".repeat(32)));
    for not in
        ["", "short", &"a".repeat(33), "AAAAAAAAAAAAAAAAAAAAA!", "AAAAAAAAAAAAAAAAAAAAA/", "ÀAAAAAAAAAAAAAAAAAAAA"]
    {
        assert!(!places::is_id(not), "{not:?}");
    }
}

#[test]
fn a_route_is_kept_as_compact_json_with_a_person_s_words_as_they_are() {
    let route = json!({"view": "m", "q": "é \"x\" <b>&'\n\u{1}\\", "n": 1.5, "big": 1e20, "small": 0.00001, "u": 3,
                       "list": [true, null]});
    assert_eq!(
        places::text_of(&route),
        r#"{"view":"m","q":"é \"x\" <b>&'\n\u0001\\","n":1.5,"big":1e+20,"small":1e-05,"u":3,"list":[true,null]}"#
    );
}

#[test]
fn a_place_kept_opens_as_its_route_and_one_never_kept_as_none() {
    let (_dir, places, database) = places();
    places.keep(&[(ID.to_owned(), json!({"view": "all"}))]).unwrap();
    assert_eq!(places.opened(ID).unwrap().as_deref(), Some(r#"{"view":"all"}"#));
    places.keep(&[(ID.to_owned(), json!({"view": "m"}))]).unwrap();
    assert_eq!(places.opened(ID).unwrap().as_deref(), Some(r#"{"view":"m"}"#));
    assert_eq!(places.opened("BBBBBBBBBBBBBBBBBBBBBB").unwrap(), None);
    assert_eq!(places.opened("short").unwrap(), None);
    let used: String = database.read(|c| c.query_row("SELECT used_at FROM places", [], |r| r.get(0))).unwrap();
    assert_eq!(used, "2026-11-02T12:00:00+00:00");
}

#[test]
fn a_batch_with_one_place_that_cant_be_kept_keeps_none() {
    let (_dir, places, database) = places();
    let too_many: Vec<_> = (0..65).map(|_| (ID.to_owned(), json!({}))).collect();
    assert_eq!(refused(places.keep(&too_many).unwrap_err()), "at most 64 places at once");
    let bad_id = [(ID.to_owned(), json!({})), ("short".to_owned(), json!({}))];
    assert_eq!(refused(places.keep(&bad_id).unwrap_err()), "a place's id is 16 to 32 letters, digits, - or _");
    assert_eq!(refused(places.keep(&[(ID.to_owned(), json!([]))]).unwrap_err()), "a place's route is a JSON object");
    let big = [(ID.to_owned(), json!({"x": "y".repeat(5000)}))];
    assert_eq!(refused(places.keep(&big).unwrap_err()), "a place's route is at most 4096 bytes");
    let kept: i64 = database.read(|c| c.query_row("SELECT count(*) FROM places", [], |r| r.get(0))).unwrap();
    assert_eq!(kept, 0);
}
