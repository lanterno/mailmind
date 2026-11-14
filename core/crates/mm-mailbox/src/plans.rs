//! A plan a preview froze, carried out (`mailbox.application.filing`'s `carry_out`): what the Assistant's bulk command
//! showed a person, and only that, each message through the route a click on it would take, under one undo group so
//! a single Undo brings it all back. A snooze goes through Attention's, which this context asks through a port.
//!
//! A copy no longer in the folder the preview saw it in is left where it is: someone moved it, and a verb that was right
//! for the Inbox may not be for wherever it went.

use std::fmt;
use std::sync::Arc;

use mm_kernel::errors::{Failure, Fault, Kind};
use mm_kernel::log::{self, Field};
use serde_json::{Value, json};

use crate::command::truthy;
use crate::command_ports::Tokens;
use crate::filing::Filing;

const TAG: &str = "do";

/// Where the copies a preview saw are now.
pub trait PlanCopies: Send + Sync + fmt::Debug {
    /// In one read, the pk of each copy, by its message's key and the folder the preview saw it in, that is still
    /// there and not on its way out, in order; the ones that have gone left out.
    fn still_there(&self, messages: &[(i64, String)]) -> Result<Vec<i64>, Fault>;
}

/// What a plan's messages go through, one at a time, as a click on each would: the Mailbox's filing.
pub trait OneByOne: Send + Sync + fmt::Debug {
    /// A move of `kind`, its reversal kept under `group`.
    fn file(&self, kind: &str, pk: i64, group: &str) -> Result<Value, Failure>;
    /// A flag change, which Undo has nothing to take back of.
    fn set_flags(&self, pk: i64, flags: &[(&str, bool)]) -> Result<Value, Failure>;
}

impl OneByOne for Filing {
    fn file(&self, kind: &str, pk: i64, group: &str) -> Result<Value, Failure> {
        Filing::file(self, kind, pk, Some(group), None, None)
    }

    fn set_flags(&self, pk: i64, flags: &[(&str, bool)]) -> Result<Value, Failure> {
        Filing::set_flags(self, pk, flags, None)
    }
}

/// What a plan asks of Attention: its messages snoozed until `preset` says, under `group`. What the snooze answers,
/// its `ok` and `count`; a preset that says no time is Refused, and none of them went.
pub trait PlanSnooze: Send + Sync + fmt::Debug {
    fn snooze(&self, pks: &[i64], preset: Option<&str>, group: &str) -> Result<Value, Failure>;
}

/// What wires `Plans`.
#[derive(Debug)]
pub struct PlansParts {
    pub filing: Arc<dyn OneByOne>,
    pub copies: Arc<dyn PlanCopies>,
    pub snooze: Arc<dyn PlanSnooze>,
    /// An undo group's name, where the request names none.
    pub tokens: Arc<dyn Tokens>,
}

#[derive(Debug)]
pub struct Plans {
    filing: Arc<dyn OneByOne>,
    copies: Arc<dyn PlanCopies>,
    snooze: Arc<dyn PlanSnooze>,
    tokens: Arc<dyn Tokens>,
}

/// How many of `res` count, as Python's `res.get(name, 0)` reads a number.
fn count_of(res: &Value) -> i64 {
    res.get("count").and_then(Value::as_i64).unwrap_or(0)
}

fn said_ok(res: &Value) -> bool {
    res.get("ok").is_some_and(truthy)
}

impl Plans {
    pub fn new(parts: PlansParts) -> Plans {
        let PlansParts { filing, copies, snooze, tokens } = parts;
        Plans { filing, copies, snooze, tokens }
    }

    /// One message of a plan, through the route a click on that message would take.
    fn one(&self, verb: &str, pk: i64, group: &str) -> Result<Value, Failure> {
        match verb {
            "read" | "unread" => self.filing.set_flags(pk, &[("seen", verb == "read")]),
            "star" => self.filing.set_flags(pk, &[("flagged", true)]),
            "trash" | "junk" => self.filing.file(verb, pk, group),
            _ => self.filing.file("archive", pk, group),
        }
    }

    /// Carry out what a plan froze, and only that, whatever else the request names: how many went, failed, had gone
    /// from where the preview saw them, and wait in the queue, and whether Undo can take any back.
    pub fn carry_out(
        &self,
        verb: &str,
        preset: Option<&str>,
        messages: &[(i64, String)],
        undo_group: Option<&str>,
    ) -> Result<Value, Failure> {
        let pks = self.copies.still_there(messages)?;
        let group = match undo_group {
            Some(g) if !g.is_empty() => g.to_owned(),
            _ => format!("plan:{}", self.tokens.hex(6)),
        };
        let gone = messages.len() - pks.len();
        let (mut count, mut failed, mut queued, mut undoable) = (0_i64, 0_i64, 0_i64, false);
        if verb == "snooze" {
            if !pks.is_empty() {
                let res = match self.snooze.snooze(&pks, preset, &group) {
                    Ok(res) => res,
                    // A preset that says no time: none of them went.
                    Err(Failure::Problem(no)) if no.kind == Kind::Refused => json!({}),
                    Err(failure) => return Err(failure),
                };
                count = count_of(&res);
                failed = i64::try_from(pks.len()).unwrap_or(i64::MAX) - count;
                undoable = said_ok(&res);
            }
        } else {
            for pk in &pks {
                let res = match self.one(verb, *pk, &group) {
                    Ok(res) => res,
                    // Counted, and nothing went wrong here.
                    Err(Failure::Problem(no)) if matches!(no.kind, Kind::NotFound | Kind::Conflict) => json!({}),
                    // The rest still go: this run has no second try.
                    Err(failure) => {
                        let class = match &failure {
                            Failure::Fault(fault) => fault.class,
                            Failure::Problem(_) => "Problem",
                        };
                        log::warning(TAG, "%(what)s failed", &[(Field::What, verb)], Some(class));
                        json!({})
                    }
                };
                if said_ok(&res) {
                    count += 1;
                    queued += i64::from(res.get("status").and_then(Value::as_str) == Some("queued"));
                    undoable = undoable || res.get("undoable").is_some_and(truthy);
                } else {
                    failed += 1;
                }
            }
        }
        Ok(json!({"ok": true, "verb": verb, "count": count, "failed": failed, "gone": gone, "queued": queued,
                  "undoable": undoable}))
    }
}
