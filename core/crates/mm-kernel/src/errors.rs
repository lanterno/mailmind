//! What a use case answers when it can't do what was asked: a `Problem`, carrying what a person is told, or a
//! `Fault`, which no person is told anything of. The Python app's `mailmind.kernel.errors`, kind for kind.
//!
//! `said` is written for that person, in the app's own words: never an error's text, or what a server said. It may be
//! empty, for an answer the page words itself. `more` is what else the answer says, flags and ids a client acts on.
//! The kernel knows nothing of HTTP: the server says which status each kind answers with.

use std::fmt;

use serde_json::Value;

/// The kinds of problem, as `mailmind.kernel.errors` names them.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Kind {
    /// What the request names isn't there, or isn't any more.
    NotFound,
    /// Things have moved on from what the request saw, or stand in its way. The same request again won't help.
    Conflict,
    /// Something holds it for now, as a queued send holds its draft.
    Locked,
    /// The request can't go as it stands: it is malformed, or it asks what can't be.
    Refused,
    /// A server or the model didn't answer. Asking again can help.
    Unreachable,
    /// It came, and can't be read. Asking again won't help.
    Unreadable,
    /// Another job holds what this one needs, and this one never ran.
    Busy,
}

/// A use case couldn't do what was asked, and says so in a person's words.
#[derive(Clone, Debug, PartialEq)]
pub struct Problem {
    pub kind: Kind,
    pub said: String,
    /// Members of the answer beside the four every problem has, in the order given.
    pub more: Vec<(&'static str, Value)>,
}

impl Problem {
    pub fn new(kind: Kind, said: impl Into<String>) -> Problem {
        let more = if kind == Kind::Busy { vec![("busy", Value::Bool(true))] } else { Vec::new() };
        Problem { kind, said: said.into(), more }
    }

    /// `Refused()`: a malformed request, which says only `bad request`.
    pub fn bad_request() -> Problem {
        Problem::new(Kind::Refused, "bad request")
    }

    pub fn refused(said: impl Into<String>) -> Problem {
        Problem::new(Kind::Refused, said)
    }

    pub fn not_found(said: impl Into<String>) -> Problem {
        Problem::new(Kind::NotFound, said)
    }

    /// The same problem, saying `value` under `name` as well.
    pub fn with(mut self, name: &'static str, value: Value) -> Problem {
        self.more.push((name, value));
        self
    }
}

/// Something went wrong that no person is told of: the answer is a fixed one, and the log names only `class`, never
/// what the error said, which can hold what a server or a message said.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Fault {
    pub class: &'static str,
}

impl Fault {
    pub const fn new(class: &'static str) -> Fault {
        Fault { class }
    }
}

/// What a use case answers when it can't do what was asked.
#[derive(Clone, Debug, PartialEq)]
pub enum Failure {
    Problem(Problem),
    Fault(Fault),
}

impl From<Problem> for Failure {
    fn from(problem: Problem) -> Failure {
        Failure::Problem(problem)
    }
}

impl From<Fault> for Failure {
    fn from(fault: Fault) -> Failure {
        Failure::Fault(fault)
    }
}

impl fmt::Display for Failure {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Failure::Problem(p) => write!(f, "{:?}: {}", p.kind, p.said),
            Failure::Fault(fault) => f.write_str(fault.class),
        }
    }
}

impl std::error::Error for Failure {}
