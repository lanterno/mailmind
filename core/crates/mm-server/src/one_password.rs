//! 1Password's CLI, which `mailmind-server` reads a secret reference through (`secrets._op_read`): `op read
//! <reference>`, tried again after a pause when op timed out or its authorization prompt went unanswered, three times
//! at most; and the sync's first round, which reads every account's 1Password credential before it syncs.
//!
//! `op` is the first program of that name in a folder the environment's PATH names, absolute folders alone: a
//! relative one is whatever the current directory is. Each start of it is asked of `Outside` first, so a test's
//! tripwire refuses it. The reference goes on its command line: it names an item and isn't a secret, and only one
//! `one_password::readable` takes is sent. The secret comes back on standard output, into a `Secret`. What op says on
//! standard error is read only for whether to try again, and goes into no log and no error. Its standard input is
//! nothing: it asks a person only through 1Password's own prompt. Its environment is the server's (`Environment`), but
//! for the two secrets in it, the database's key and the Claude API key, which it has no use for.

use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::Arc;
use std::time::{Duration, Instant};

use mm_accounts::Directory;
use mm_accounts::one_password::{UNREAD, readable};
use mm_accounts::ports::OnePassword;
use mm_kernel::errors::Fault;
use mm_kernel::log::{self, Field};
use mm_kernel::seams::{Outside, Secret};

use crate::environment::Environment;

/// The program 1Password is read through.
pub const OP: &str = "op";
/// No folder PATH names holds op.
pub const NOT_FOUND: Fault = Fault::new("OpNotFound");
/// How many times a read is tried.
const TRIES: usize = 3;
/// The pause before a read op said timed out is tried again.
const PAUSE: Duration = Duration::from_secs(3);
/// How long one start may take: the authorization prompt may be waiting for someone.
const TIMEOUT: Duration = Duration::from_secs(60);
/// How often a running op is looked in on.
const POLL: Duration = Duration::from_millis(5);
/// The variables that hold a secret of the server's own, which op isn't given.
const WITHHELD: [&str; 2] = [crate::environment::DB_KEY, "ANTHROPIC_API_KEY"];
const TAG: &str = "sync";

/// `op read`, through the op a PATH finds.
pub struct OpCli {
    /// The folders op is looked for in, in turn.
    folders: Vec<PathBuf>,
    /// What op's environment holds: the server's, but its own secrets. A variable an account's credential names may
    /// be one, so `Debug` never shows them.
    handed: Vec<(String, String)>,
    outside: Arc<dyn Outside>,
    timeout: Duration,
    pause: Duration,
}

impl std::fmt::Debug for OpCli {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("OpCli").field("folders", &self.folders).finish_non_exhaustive()
    }
}

/// How one start of op ended.
enum Ended {
    Read(Secret),
    /// It timed out, or nobody answered its prompt: worth another try.
    Again,
    Failed,
}

/// Whether `path` is a file someone may run.
fn runnable(path: &Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::metadata(path).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
    }
    #[cfg(not(unix))]
    {
        path.is_file()
    }
}

/// Whether what op said on standard error asks for another try (`"timeout"` or `"authorization"`, in any case).
fn worth_again(said: &[u8]) -> bool {
    let said = String::from_utf8_lossy(said).to_lowercase();
    said.contains("timeout") || said.contains("authorization")
}

impl OpCli {
    /// op as `environment`'s PATH finds it, started in that environment.
    pub fn new(environment: &Environment, outside: Arc<dyn Outside>) -> OpCli {
        OpCli::through(environment, outside, TIMEOUT, PAUSE)
    }

    /// op as `environment`'s PATH finds it, given `timeout` for each start and `pause` before another try.
    pub fn through(environment: &Environment, outside: Arc<dyn Outside>, timeout: Duration, pause: Duration) -> OpCli {
        let path = environment.program_path().unwrap_or_default();
        let folders = std::env::split_paths(path).filter(|f| f.is_absolute()).collect();
        OpCli { folders, handed: environment.passed_on(&WITHHELD), outside, timeout, pause }
    }

    /// The op the first folder holding one has.
    fn program(&self) -> Option<PathBuf> {
        self.folders.iter().map(|folder| folder.join(OP)).find(|program| runnable(program))
    }

    /// One start of op for `reference`, waited for, at most `timeout`.
    fn once(&self, reference: &str) -> Result<Ended, Fault> {
        self.outside.run(OP).map_err(|_| UNREAD)?;
        let program = self.program().ok_or(NOT_FOUND)?;
        let mut child = Command::new(program)
            .env_clear()
            .envs(self.handed.iter().map(|(name, value)| (name, value)))
            .arg("read")
            .arg(reference)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|_| UNREAD)?;
        let (mut stdout, mut stderr) = (child.stdout.take().ok_or(UNREAD)?, child.stderr.take().ok_or(UNREAD)?);
        let reading = std::thread::spawn(move || {
            let mut out = Vec::new();
            let _ = stdout.read_to_end(&mut out);
            out
        });
        let hearing = std::thread::spawn(move || {
            let mut said = Vec::new();
            let _ = stderr.read_to_end(&mut said);
            said
        });
        // Past its time, it is stopped and its pipes let go: what it started may still hold them open.
        let status = self.wait(&mut child)?;
        let mut out = reading.join().map_err(|_| UNREAD)?;
        let said = hearing.join().map_err(|_| UNREAD)?;
        if !status.success() {
            out.fill(0);
            return Ok(if worth_again(&said) { Ended::Again } else { Ended::Failed });
        }
        match String::from_utf8(out) {
            Ok(text) => Ok(Ended::Read(Secret::new(text))),
            Err(not_text) => {
                not_text.into_bytes().fill(0);
                Ok(Ended::Failed)
            }
        }
    }

    /// op's end, or a fault once it has run past `timeout`, when it is stopped.
    fn wait(&self, child: &mut Child) -> Result<ExitStatus, Fault> {
        let deadline = Instant::now() + self.timeout;
        loop {
            match child.try_wait() {
                Ok(Some(status)) => return Ok(status),
                Ok(None) if Instant::now() < deadline => std::thread::sleep(POLL),
                _ => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(UNREAD);
                }
            }
        }
    }
}

impl OnePassword for OpCli {
    fn read(&self, reference: &str) -> Result<Secret, Fault> {
        if !readable(reference) {
            return Err(UNREAD);
        }
        for tried in 0..TRIES {
            if tried > 0 {
                std::thread::sleep(self.pause);
            }
            match self.once(reference)? {
                Ended::Read(secret) => return Ok(secret),
                Ended::Again => continue,
                Ended::Failed => return Err(UNREAD),
            }
        }
        Err(UNREAD)
    }
}

/// Before the sync's first round, every account's 1Password credential read (`sync_rounds`), so a long-running server
/// needn't ask again: how many read is logged when any did.
pub(crate) fn warm(directory: &Directory) {
    match directory.preload() {
        Ok(0) => {}
        Ok(read) => {
            log::info(TAG, "warmed %(count)s 1Password-backed credential(s)", &[(Field::Count, &read.to_string())])
        }
        Err(fault) => log::warning(TAG, "the 1Password credentials weren't read ahead", &[], Some(fault.class)),
    }
}
