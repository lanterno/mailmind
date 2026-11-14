//! The environment, read here and nowhere else in the server: the variables `mailmind.platform.environment`
//! declares, read as it reads them. A variable this server doesn't act on yet is still declared, so the table says
//! what each one is for whichever server reads it.

use std::collections::HashMap;
use std::path::{Component, Path, PathBuf};

use mm_kernel::seams::Secret;

/// The data directory (settings). Unset or empty, the repo root. It must exist.
pub const HOME: &str = "MAILMIND_HOME";
/// The key the database is encrypted with, 64 hex digits, in place of the Keychain's: for the tests, the demo and a
/// computer with no Keychain.
pub const DB_KEY: &str = "MAILMIND_DB_KEY";

/// Every variable the app reads, and what it is for.
pub const VARIABLES: [(&str, &str); 18] = [
    (HOME, "The data directory (settings). Unset, the repo root. It must exist."),
    ("MAILMIND_PROFILE", "How the process runs (profile): full, or read-only, which writes nothing outside."),
    ("MAILMIND_NOSYNC", "The older switch for the read-only profile, which wins over MAILMIND_PROFILE."),
    ("MAILMIND_WORKERS", "One gate for every account's IMAP work (global), or one each (account) (gates)."),
    ("MAILMIND_POOL", "Keep an IMAP connection for each account named, for reads (mailbox.pool)."),
    ("MAILMIND_WARM_FOLDERS", "Sync each named account's Drafts, Archive and Sent with its INBOX."),
    ("MAILMIND_LOG_LEVEL", "debug shows debug lines and each error's text at the terminal, never in the file."),
    ("MAILMIND_RECONCILE_SECS", "About how long reconcile takes to go round every account."),
    ("MAILMIND_EXPIRE_SECS", "The seconds between the expiry rules' rounds."),
    ("MAILMIND_NO_DRAFT_TICK", "Draft no replies ahead (the draft-ahead job)."),
    ("MAILMIND_NO_BATCHES", "Triage a backlog one message at a time, at full price, rather than in batches."),
    ("MAILMIND_NO_SCHEMA", "Ask the model for free-form JSON rather than structured output."),
    ("MAILMIND_MODEL", "The Claude model every feature asks."),
    ("MAILMIND_PROTON_BRIDGE_PASS", "The Proton Bridge password, before any other source."),
    ("MAILMIND_PROTON_OP_ITEM", "The 1Password item holding the Proton Bridge password."),
    ("ANTHROPIC_API_KEY", "The Claude API key, when mailmind.config.json names none. Neither: AI is off."),
    ("ANTHROPIC_BASE_URL", "Where API calls go, in place of https://api.anthropic.com."),
    (DB_KEY, "The key the database is encrypted with, 64 hex digits, in place of the Keychain's."),
];

/// The repo root, where the data lives when MAILMIND_HOME names nowhere: this crate's, three folders up.
pub fn repo_root() -> PathBuf {
    let crate_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    crate_dir.ancestors().nth(3).unwrap_or(crate_dir).to_path_buf()
}

/// `os.path.abspath`: from the current directory, with `.` and `..` worked out from the text alone.
fn absolute(path: &str) -> std::io::Result<PathBuf> {
    let mut out = PathBuf::new();
    for part in std::path::absolute(path)?.components() {
        match part {
            Component::ParentDir => {
                out.pop();
            }
            Component::CurDir => {}
            other => out.push(other),
        }
    }
    Ok(out)
}

/// The environment as the process was given it, or as a caller says it is.
#[derive(Clone, Default)]
pub struct Environment {
    vars: HashMap<String, String>,
}

/// Names only: a value may be a secret (the database's key, the Claude API key, a variable a credential names).
impl std::fmt::Debug for Environment {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let mut names: Vec<&str> = self.vars.keys().map(String::as_str).collect();
        names.sort_unstable();
        f.debug_struct("Environment").field("names", &names).finish()
    }
}

/// An account's credential that names a variable reads it here.
impl mm_accounts::ports::Variables for Environment {
    fn variable(&self, name: &str) -> Option<Secret> {
        self.credential(name)
    }
}

/// Where the app keeps its data, and where it is served.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Settings {
    /// The data directory.
    pub home: PathBuf,
}

impl Settings {
    pub fn db(&self) -> PathBuf {
        self.home.join("mailmind.db")
    }

    pub fn config(&self) -> PathBuf {
        self.home.join("mailmind.config.json")
    }
}

impl Environment {
    /// The process's own, read once. A variable that isn't text reads as unset.
    pub fn of_process() -> Environment {
        Environment {
            vars: std::env::vars_os()
                .filter_map(|(k, v)| Some((k.into_string().ok()?, v.into_string().ok()?)))
                .collect(),
        }
    }

    pub fn from_pairs<'a>(pairs: impl IntoIterator<Item = (&'a str, &'a str)>) -> Environment {
        Environment { vars: pairs.into_iter().map(|(k, v)| (k.to_owned(), v.to_owned())).collect() }
    }

    /// A variable as set, None when unset.
    pub fn raw(&self, name: &str) -> Option<&str> {
        self.vars.get(name).map(String::as_str)
    }

    /// Settings from MAILMIND_HOME (`settings.from_env`): empty counts as unset, a relative one is taken from the
    /// current directory, and a leading `~` is the home folder's. It must be a directory.
    pub fn settings(&self) -> Result<Settings, String> {
        let raw = self.raw(HOME).unwrap_or_default();
        if raw.is_empty() {
            return Ok(Settings { home: repo_root() });
        }
        let expanded = match (raw.strip_prefix('~'), self.raw("HOME")) {
            (Some(rest), Some(home)) if rest.is_empty() || rest.starts_with('/') => format!("{home}{rest}"),
            _ => raw.to_owned(),
        };
        let home = absolute(&expanded).map_err(|_| format!("{HOME} is not a directory: {expanded}"))?;
        if !home.is_dir() {
            return Err(format!("{HOME} is not a directory: {}", home.display()));
        }
        Ok(Settings { home })
    }

    /// How the process runs (`profile.current`): `read-only` when MAILMIND_NOSYNC is on, or is a value it doesn't
    /// take, or MAILMIND_PROFILE names no profile; else MAILMIND_PROFILE's, `full` when unset.
    pub fn profile(&self) -> &'static str {
        let given = |name: &str| mm_kernel::text::strip(self.raw(name).unwrap_or_default()).to_lowercase();
        if !matches!(given("MAILMIND_NOSYNC").as_str(), "" | "0" | "false" | "no" | "off") {
            return "read-only";
        }
        match given("MAILMIND_PROFILE").as_str() {
            "" | "full" => "full",
            _ => "read-only",
        }
    }

    /// The folder for temporary files, as `tempfile.gettempdir` finds it: TMPDIR, TEMP or TMP, else `/tmp`.
    pub fn temporary(&self) -> PathBuf {
        ["TMPDIR", "TEMP", "TMP"]
            .iter()
            .filter_map(|name| self.raw(name).filter(|v| !v.is_empty()))
            .map(PathBuf::from)
            .find(|folder| folder.is_dir())
            .unwrap_or_else(|| PathBuf::from("/tmp"))
    }

    /// Whether ANTHROPIC_API_KEY gives a key: set, and not blank.
    pub fn gives_anthropic_key(&self) -> bool {
        !mm_kernel::text::strip(self.raw("ANTHROPIC_API_KEY").unwrap_or_default()).is_empty()
    }

    /// `TZ`, which no MailMind variable is: the zone the C library takes for the person's own, as Python's local
    /// times read it. None when it is unset.
    pub fn zone(&self) -> Option<&str> {
        self.raw("TZ")
    }

    /// The database key the environment gives, if it gives one.
    pub fn db_key(&self) -> Option<Secret> {
        self.raw(DB_KEY).filter(|k| !k.is_empty()).map(|k| Secret::new(k.to_owned()))
    }

    /// Whether this process only reads, writing nothing to a mail server (`profile.current`): MAILMIND_NOSYNC on,
    /// or a value it doesn't take, wins; else MAILMIND_PROFILE, unset or empty `full`, and a name that is no
    /// profile read-only, so it fails safe.
    pub fn read_only(&self) -> bool {
        let given = |name: &str| self.raw(name).unwrap_or_default().trim().to_lowercase();
        let nosync = given("MAILMIND_NOSYNC");
        if !["", "0", "false", "no", "off"].contains(&nosync.as_str()) {
            return true;
        }
        let profile = given("MAILMIND_PROFILE");
        !(profile.is_empty() || profile == "full")
    }

    /// MAILMIND_RECONCILE_SECS: about how long reconcile takes to go round every account, in seconds, 0 for never.
    /// Unset, empty or not a whole number, 1800.
    pub fn reconcile_secs(&self) -> i64 {
        let given = self.raw("MAILMIND_RECONCILE_SECS").map(str::trim).filter(|v| !v.is_empty());
        given.and_then(|v| v.parse().ok()).unwrap_or(1800)
    }

    /// MAILMIND_EXPIRE_SECS: the seconds between the expiry rules' rounds, 0 or below for none. Unset, empty or not a
    /// whole number, 3600.
    pub fn expire_secs(&self) -> i64 {
        let given = self.raw("MAILMIND_EXPIRE_SECS").map(str::trim).filter(|v| !v.is_empty());
        given.and_then(|v| v.parse().ok()).unwrap_or(3600)
    }

    /// Whether each account's IMAP work passes a gate of its own (MAILMIND_WORKERS `account`, in any case, with
    /// spaces around); unset, or any other value, one gate is every account's.
    pub fn gate_per_account(&self) -> bool {
        self.raw("MAILMIND_WORKERS").unwrap_or_default().trim().eq_ignore_ascii_case("account")
    }

    // The Assistant's.

    /// ANTHROPIC_API_KEY, without the spaces around it: None when unset or blank.
    pub fn anthropic_key(&self) -> Option<Secret> {
        let key = mm_kernel::text::strip(self.raw("ANTHROPIC_API_KEY").unwrap_or_default());
        (!key.is_empty()).then(|| Secret::new(key.to_owned()))
    }

    /// ANTHROPIC_BASE_URL: where API calls go, None for the API's own address.
    pub fn anthropic_base_url(&self) -> Option<String> {
        self.raw("ANTHROPIC_BASE_URL").filter(|url| !url.is_empty()).map(str::to_owned)
    }

    /// MAILMIND_MODEL: the Claude model every feature asks, None for the default.
    pub fn model(&self) -> Option<String> {
        Some(self.raw("MAILMIND_MODEL")?.trim().to_owned()).filter(|m| !m.is_empty())
    }

    /// MAILMIND_NO_SCHEMA on: free-form JSON rather than structured output. A value it doesn't take is off.
    pub fn no_schema(&self) -> bool {
        ["1", "true", "yes", "on"]
            .contains(&self.raw("MAILMIND_NO_SCHEMA").unwrap_or_default().trim().to_lowercase().as_str())
    }

    /// MAILMIND_NO_BATCHES on: a backlog is triaged one message at a time, at full price, rather than sent as a batch.
    /// A value it doesn't take is off.
    pub fn no_batches(&self) -> bool {
        ["1", "true", "yes", "on"]
            .contains(&self.raw("MAILMIND_NO_BATCHES").unwrap_or_default().trim().to_lowercase().as_str())
    }

    /// MAILMIND_NO_DRAFT_TICK on: draft no replies ahead. A value it doesn't take is on, which drafts nothing.
    pub fn no_draft_tick(&self) -> bool {
        !["", "0", "false", "no", "off"]
            .contains(&self.raw("MAILMIND_NO_DRAFT_TICK").unwrap_or_default().trim().to_lowercase().as_str())
    }

    /// PATH: the folders a program named alone is looked for in, as 1Password's `op` is.
    pub fn program_path(&self) -> Option<&str> {
        self.raw("PATH")
    }

    /// Every variable but those `withheld`, for the environment of a program the server starts (`op`).
    pub fn passed_on(&self, withheld: &[&str]) -> Vec<(String, String)> {
        let kept = self.vars.iter().filter(|(name, _)| !withheld.contains(&name.as_str()));
        kept.map(|(name, value)| (name.clone(), value.clone())).collect()
    }

    /// The variable an account's or a setting's credential names (`{"env": NAME}` in mailmind.config.json), as set.
    pub fn credential(&self, name: &str) -> Option<Secret> {
        self.raw(name).map(|value| Secret::new(value.to_owned()))
    }

    /// MAILMIND_WARM_FOLDERS, when set and not blank: on (or `all`) for every account, off for none, else the
    /// addresses it lists. Unset, each account's entry in the file decides.
    pub fn warm_folders(&self) -> Option<mm_mailbox::warm::Which> {
        mm_mailbox::warm::Which::read(self.raw("MAILMIND_WARM_FOLDERS"))
    }
}
