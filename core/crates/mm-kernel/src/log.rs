//! What the core says of its own running: a line a person reading the log may see.
//!
//! A log is personal data, so a line holds only what the core can vouch for. Its message is a literal, with
//! `%(name)s` where a field goes, and a field's name is a `Field`, the Python app's `ALLOWED`: the app's own words,
//! ids and counts, never a person's. Even there, anything that looks like an address is withheld when the line is
//! written. Of an error, only its class shows.
//!
//! The program installs where lines go (`install`); until it does, they go nowhere. Nothing here writes anything.

use std::fmt;
use std::sync::OnceLock;

/// How grave a line is.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Level {
    Debug,
    Info,
    Warning,
    Error,
}

impl Level {
    pub fn name(self) -> &'static str {
        match self {
            Level::Debug => "DEBUG",
            Level::Info => "INFO",
            Level::Warning => "WARNING",
            Level::Error => "ERROR",
        }
    }
}

/// The names a line may show a value under: the Python app's `logging.ALLOWED`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Field {
    Adapters,
    Count,
    Engine,
    Event,
    Handler,
    Id,
    Job,
    Method,
    Path,
    Port,
    What,
    Why,
}

impl Field {
    pub fn name(self) -> &'static str {
        match self {
            Field::Adapters => "adapters",
            Field::Count => "count",
            Field::Engine => "engine",
            Field::Event => "event",
            Field::Handler => "handler",
            Field::Id => "id",
            Field::Job => "job",
            Field::Method => "method",
            Field::Path => "path",
            Field::Port => "port",
            Field::What => "what",
            Field::Why => "why",
        }
    }
}

/// One line: whose it is (`[tag]`), its literal message, the fields it names and the class of what went wrong.
#[derive(Clone, Debug)]
pub struct Line<'a> {
    pub level: Level,
    pub tag: &'static str,
    pub message: &'static str,
    pub fields: &'a [(Field, &'a str)],
    pub error: Option<&'static str>,
}

/// Where lines go.
pub trait Sink: Send + Sync + fmt::Debug {
    fn write(&self, line: &Line<'_>);
}

static SINK: OnceLock<Box<dyn Sink>> = OnceLock::new();

/// Send every line to `sink` from now on. The first sink a process installs is the one it keeps.
pub fn install(sink: Box<dyn Sink>) {
    let _ = SINK.set(sink);
}

pub fn line(line: &Line<'_>) {
    if let Some(sink) = SINK.get() {
        sink.write(line);
    }
}

pub fn warning(tag: &'static str, message: &'static str, fields: &[(Field, &str)], error: Option<&'static str>) {
    line(&Line { level: Level::Warning, tag, message, fields, error });
}

pub fn error(tag: &'static str, message: &'static str, fields: &[(Field, &str)], error: Option<&'static str>) {
    line(&Line { level: Level::Error, tag, message, fields, error });
}

pub fn info(tag: &'static str, message: &'static str, fields: &[(Field, &str)]) {
    line(&Line { level: Level::Info, tag, message, fields, error: None });
}
