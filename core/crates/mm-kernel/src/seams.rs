//! The seams through which a program meets the world: the time, files, hosts, other programs and the Keychain.
//!
//! A use case reads the time through a `Clock` and a secret through a `Keychain`; whatever opens a file, reaches a
//! host or starts a program asks `Outside` first. The program wires the real ones; a test launcher wires a frozen
//! clock, a Keychain of its own and a tripwire, so the app itself holds no test hooks. Nothing here does any of it.

use std::fmt;
use std::path::Path;

use zeroize::Zeroizing;

/// A moment, in microseconds since the Unix epoch, UTC.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Moment(pub i64);

impl Moment {
    pub const fn from_micros(micros: i64) -> Moment {
        Moment(micros)
    }

    pub const fn micros(self) -> i64 {
        self.0
    }

    /// Seconds since the epoch, as Python's `time.time()` gives them.
    pub fn seconds(self) -> f64 {
        self.0 as f64 / 1_000_000.0
    }

    /// Whole seconds since the epoch, rounded down.
    pub const fn whole_seconds(self) -> i64 {
        self.0.div_euclid(1_000_000)
    }
}

/// Where a use case reads the time.
pub trait Clock: Send + Sync + fmt::Debug {
    fn now(&self) -> Moment;
}

/// What a tripwire refused: the act, in its own words, for the test that tripped it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Tripped(pub String);

impl fmt::Display for Tripped {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "tripwire: {}", self.0)
    }
}

impl std::error::Error for Tripped {}

/// Asked before a file is opened, a host reached or a program started. The program's own lets everything through;
/// a test launcher's refuses what a test must never touch, and remembers it.
pub trait Outside: Send + Sync + fmt::Debug {
    /// Before the file at `path` is opened, made, replaced or removed.
    fn open(&self, path: &Path) -> Result<(), Tripped>;
    /// Before a connection to `host`, a name or an address, is made or its name looked up.
    fn reach(&self, host: &str) -> Result<(), Tripped>;
    /// Before `program` is started.
    fn run(&self, program: &str) -> Result<(), Tripped>;
}

/// A secret: a password or a key. It never prints, and its bytes are wiped when it is dropped.
pub struct Secret(Zeroizing<String>);

impl Secret {
    pub fn new(text: String) -> Secret {
        Secret(Zeroizing::new(text))
    }

    /// The secret itself, for the one place it goes.
    pub fn expose(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for Secret {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Secret(..)")
    }
}

/// The Keychain didn't answer: locked, or not there. It says nothing of what was asked.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct KeychainProblem;

impl fmt::Display for KeychainProblem {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("the Keychain didn't answer")
    }
}

impl std::error::Error for KeychainProblem {}

/// The login Keychain, where MailMind keeps its secrets as generic passwords of the service `mailmind`, each under an
/// item's name (`account:<address>`, `anthropic-api-key`).
///
/// A Keychain that only reads keeps nothing and takes nothing out: `put` and `delete` say it didn't answer.
pub trait Keychain: Send + Sync + fmt::Debug {
    /// The secret kept under `item`, or None when there is none.
    fn find(&self, item: &str) -> Result<Option<Secret>, KeychainProblem>;

    /// Keep `secret` under `item`, in place of whatever was there. Done only once the Keychain holds it.
    fn put(&self, _item: &str, _secret: &Secret) -> Result<(), KeychainProblem> {
        Err(KeychainProblem)
    }

    /// Take `item` out: false when there was none.
    fn delete(&self, _item: &str) -> Result<bool, KeychainProblem> {
        Err(KeychainProblem)
    }
}

/// Whether `name` can name an item MailMind keeps. A name goes on a command line, so it is letters, digits and
/// `. _ % + @ : = -` alone, and starts with a letter or a digit.
pub fn keepable_name(name: &str) -> bool {
    let mut chars = name.chars();
    chars.next().is_some_and(|c| c.is_ascii_alphanumeric())
        && chars.all(|c| c.is_ascii_alphanumeric() || "._%+@:=-".contains(c))
}

/// Whether `secret` is one MailMind keeps: printable ASCII, and not empty. The Keychain gives back any other as hex,
/// which can't be told from a secret that is hex.
pub fn keepable_secret(secret: &str) -> bool {
    !secret.is_empty() && secret.bytes().all(|b| (0x20..=0x7e).contains(&b))
}
