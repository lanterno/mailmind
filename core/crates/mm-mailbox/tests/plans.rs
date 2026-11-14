//! A plan a preview froze, carried out (`Plans::carry_out`): each message as a click on it would go, under one undo
//! group, a copy that has gone from where the preview saw it left alone, and a snooze through Attention's. What it
//! answers is `PlanCarriedOut`.

use std::sync::{Arc, Mutex};

use mm_kernel::errors::{Failure, Fault, Kind, Problem};
use mm_mailbox::command_ports::Tokens;
use mm_mailbox::plans::{OneByOne, PlanCopies, PlanSnooze, Plans, PlansParts};
use serde_json::{Value, json};

/// What the plan asked of the world, in order.
#[derive(Debug, Default)]
struct World {
    /// The pks still there, by the key the preview saw.
    there: Vec<(i64, i64)>,
    /// What filing one message answers, by its pk; filed where none is said.
    answers: Vec<(i64, Result<Value, Failure>)>,
    /// What a snooze answers; the count of pks where none is said.
    snoozed: Option<Result<Value, Failure>>,
    asked: Mutex<Vec<String>>,
}

impl World {
    fn note(&self, what: String) {
        self.asked.lock().unwrap_or_else(std::sync::PoisonError::into_inner).push(what);
    }

    fn asked(&self) -> Vec<String> {
        self.asked.lock().unwrap_or_else(std::sync::PoisonError::into_inner).clone()
    }

    fn answer(&self, pk: i64, undoable: bool) -> Result<Value, Failure> {
        match self.answers.iter().find(|(p, _)| *p == pk) {
            Some((_, said)) => said.clone(),
            None => Ok(json!({"ok": true, "msg": "archived", "undoable": undoable})),
        }
    }
}

impl PlanCopies for World {
    fn still_there(&self, messages: &[(i64, String)]) -> Result<Vec<i64>, Fault> {
        self.note(format!("read {}", messages.len()));
        Ok(messages
            .iter()
            .filter_map(|(key, _)| self.there.iter().find(|(k, _)| k == key).map(|(_, pk)| *pk))
            .collect())
    }
}

impl OneByOne for World {
    fn file(&self, kind: &str, pk: i64, group: &str) -> Result<Value, Failure> {
        self.note(format!("{kind} {pk} {group}"));
        self.answer(pk, true)
    }

    fn set_flags(&self, pk: i64, flags: &[(&str, bool)]) -> Result<Value, Failure> {
        self.note(format!("flags {pk} {flags:?}"));
        self.answer(pk, false)
    }
}

impl PlanSnooze for World {
    fn snooze(&self, pks: &[i64], preset: Option<&str>, group: &str) -> Result<Value, Failure> {
        self.note(format!("snooze {pks:?} {preset:?} {group}"));
        self.snoozed.clone().unwrap_or_else(|| Ok(json!({"ok": true, "count": pks.len(), "until": 1})))
    }
}

#[derive(Debug)]
struct Drawn;

impl Tokens for Drawn {
    fn hex(&self, bytes: usize) -> String {
        "ab".repeat(bytes)
    }
}

fn plans(world: &Arc<World>) -> Plans {
    Plans::new(PlansParts {
        filing: world.clone(),
        copies: world.clone(),
        snooze: world.clone(),
        tokens: Arc::new(Drawn),
    })
}

fn seen(keys: &[i64]) -> Vec<(i64, String)> {
    keys.iter().map(|k| (*k, "INBOX".to_owned())).collect()
}

fn carried(verb: &str, count: i64, failed: i64, gone: i64, queued: i64, undoable: bool) -> Value {
    json!({"ok": true, "verb": verb, "count": count, "failed": failed, "gone": gone, "queued": queued,
           "undoable": undoable})
}

fn world(there: &[(i64, i64)]) -> World {
    World { there: there.to_vec(), ..World::default() }
}

#[test]
fn it_files_what_the_preview_froze_under_the_group_the_request_names() {
    let world = Arc::new(world(&[(1, 11), (2, 12)]));
    let done = plans(&world).carry_out("archive", Some("tomorrow"), &seen(&[1, 2]), Some("plan-7"));
    assert_eq!(done, Ok(carried("archive", 2, 0, 0, 0, true)));
    assert_eq!(world.asked(), ["read 2", "archive 11 plan-7", "archive 12 plan-7"]);
}

#[test]
fn a_request_that_names_no_group_gets_one_of_its_own() {
    for named in [None, Some("")] {
        let world = Arc::new(world(&[(1, 11)]));
        plans(&world).carry_out("trash", None, &seen(&[1]), named).expect("carried out");
        assert_eq!(world.asked(), ["read 1", "trash 11 plan:abababababab"]);
    }
}

#[test]
fn each_verb_goes_the_way_a_click_on_one_message_would() {
    for (verb, asked) in [
        ("archive", "archive 11 g"),
        ("trash", "trash 11 g"),
        ("junk", "junk 11 g"),
        ("read", r#"flags 11 [("seen", true)]"#),
        ("unread", r#"flags 11 [("seen", false)]"#),
        ("star", r#"flags 11 [("flagged", true)]"#),
    ] {
        let world = Arc::new(world(&[(1, 11)]));
        let done = plans(&world).carry_out(verb, Some("tomorrow"), &seen(&[1]), Some("g")).expect("carried out");
        assert_eq!(world.asked(), ["read 1", asked], "{verb}");
        // A flag change has nothing for Undo to take back.
        assert_eq!(done, carried(verb, 1, 0, 0, 0, !verb.contains("read") && verb != "star"), "{verb}");
    }
}

#[test]
fn a_copy_gone_from_where_the_preview_saw_it_is_left_alone_and_counted() {
    let world = Arc::new(world(&[(2, 12)]));
    let done = plans(&world).carry_out("archive", None, &seen(&[1, 2, 3]), Some("g"));
    assert_eq!(done, Ok(carried("archive", 1, 0, 2, 0, true)));
    assert_eq!(world.asked(), ["read 3", "archive 12 g"]);
}

#[test]
fn with_every_copy_gone_nothing_is_filed() {
    for verb in ["archive", "snooze"] {
        let world = Arc::new(world(&[]));
        let done = plans(&world).carry_out(verb, Some("tomorrow"), &seen(&[1]), Some("g"));
        assert_eq!(done, Ok(carried(verb, 0, 0, 1, 0, false)), "{verb}");
        assert_eq!(world.asked(), ["read 1"], "{verb}");
    }
}

#[test]
fn a_message_that_fails_is_counted_and_the_rest_go_on() {
    let world = Arc::new(World {
        answers: vec![
            (11, Err(Problem::not_found("not found").into())),
            (12, Err(Problem::new(Kind::Conflict, "the server refused the archive").into())),
            (13, Err(Fault::new("Storage").into())),
            (14, Err(Problem::refused("bad request").into())),
            (15, Ok(json!({}))),
        ],
        ..world(&[(1, 11), (2, 12), (3, 13), (4, 14), (5, 15), (6, 16)])
    });
    let done = plans(&world).carry_out("archive", None, &seen(&[1, 2, 3, 4, 5, 6]), Some("g"));
    assert_eq!(done, Ok(carried("archive", 1, 5, 0, 0, true)));
    assert_eq!(world.asked().len(), 7, "every one was tried");
}

#[test]
fn only_a_move_the_queue_holds_counts_as_queued() {
    let world = Arc::new(World {
        answers: vec![
            (11, Ok(json!({"ok": true, "msg": "queued", "status": "queued", "undoable": true}))),
            (12, Ok(json!({"ok": true, "msg": "queued", "status": "waiting", "undoable": true}))),
            (13, Ok(json!({"ok": true, "msg": "cancelled", "undoable": false}))),
        ],
        ..world(&[(1, 11), (2, 12), (3, 13)])
    });
    let done = plans(&world).carry_out("trash", None, &seen(&[1, 2, 3]), Some("g"));
    assert_eq!(done, Ok(carried("trash", 3, 0, 0, 1, true)));
}

#[test]
fn a_plan_whose_moves_none_can_undo_says_so() {
    let world = Arc::new(World {
        answers: vec![(11, Ok(json!({"ok": true, "msg": "queued", "status": "queued", "undoable": false})))],
        ..world(&[(1, 11)])
    });
    assert_eq!(plans(&world).carry_out("archive", None, &seen(&[1]), None), Ok(carried("archive", 1, 0, 0, 1, false)));
}

#[test]
fn a_snooze_goes_through_attention_all_at_once_under_the_group() {
    let world = Arc::new(world(&[(1, 11), (2, 12)]));
    let done = plans(&world).carry_out("snooze", Some("weekend"), &seen(&[1, 2, 3]), Some("g"));
    assert_eq!(done, Ok(carried("snooze", 2, 0, 1, 0, true)));
    assert_eq!(world.asked(), ["read 3", r#"snooze [11, 12] Some("weekend") g"#]);
}

#[test]
fn a_snooze_counts_what_attention_says_it_snoozed() {
    let world = Arc::new(World { snoozed: Some(Ok(json!({"ok": true, "count": 1}))), ..world(&[(1, 11), (2, 12)]) });
    let done = plans(&world).carry_out("snooze", Some("tomorrow"), &seen(&[1, 2]), Some("g"));
    assert_eq!(done, Ok(carried("snooze", 1, 1, 0, 0, true)));
}

#[test]
fn a_preset_that_says_no_time_snoozes_none() {
    let world = Arc::new(World {
        snoozed: Some(Err(Problem::refused("bad snooze time").into())),
        ..world(&[(1, 11), (2, 12)])
    });
    let done = plans(&world).carry_out("snooze", None, &seen(&[1, 2]), Some("g"));
    assert_eq!(done, Ok(carried("snooze", 0, 2, 0, 0, false)));
}

#[test]
fn a_snooze_that_fails_otherwise_says_so() {
    let world = Arc::new(World { snoozed: Some(Err(Fault::new("Storage").into())), ..world(&[(1, 11)]) });
    let done = plans(&world).carry_out("snooze", Some("tomorrow"), &seen(&[1]), Some("g"));
    assert_eq!(done, Err(Failure::Fault(Fault::new("Storage"))));
}
