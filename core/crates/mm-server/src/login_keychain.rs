//! The login Keychain, where `mailmind-server` keeps a secret it's given (`mailmind.platform.keychain`).
//!
//! A secret is a generic password whose service is `mailmind` and whose account is the item's name. MailMind reaches
//! the Keychain only through /usr/bin/security, which an item it adds trusts, so reading one back asks nobody. Each
//! start of the program is asked of `Outside` first, so a test's tripwire refuses it.
//!
//! A secret goes in on the program's standard input (`security -i`), never on its command line, where another program
//! could read it, and as hex (`-X`), so no character of it is read as the line's syntax. Nothing the program says
//! goes into a log or an error: it can echo what it was handed. An item's name goes on a command line, so it is one
//! `keepable_name` takes. `security -i` can end well when the command it ran didn't, so what was kept is read back
//! before a keep is done.

use std::io::{Read, Write};
use std::path::PathBuf;
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use mm_kernel::seams::{Keychain, KeychainProblem, Outside, Secret, keepable_name, keepable_secret};

/// The program the Keychain is reached through.
pub const SECURITY: &str = "/usr/bin/security";
/// The service MailMind's items are kept under.
const SERVICE: &str = "mailmind";
/// `security`'s exit status for an item that isn't there.
const NOT_FOUND: i32 = 44;
/// How long the program may take: an unlock prompt may be waiting for someone.
const TIMEOUT: Duration = Duration::from_secs(60);
/// How often a running program is looked in on.
const POLL: Duration = Duration::from_millis(5);

#[derive(Debug)]
pub struct LoginKeychain {
    program: PathBuf,
    outside: Arc<dyn Outside>,
    timeout: Duration,
    /// A keep and its reading back, one at a time.
    keeping: Mutex<()>,
}

/// How the program ended, and what it wrote, which may be a secret.
struct Said {
    status: Option<i32>,
    out: Secret,
}

impl LoginKeychain {
    /// The login Keychain, through /usr/bin/security.
    pub fn new(outside: Arc<dyn Outside>) -> LoginKeychain {
        LoginKeychain::through(PathBuf::from(SECURITY), outside, TIMEOUT)
    }

    /// The Keychain through `program`, which speaks as `security` does, given `timeout` for each start.
    pub fn through(program: PathBuf, outside: Arc<dyn Outside>, timeout: Duration) -> LoginKeychain {
        LoginKeychain { program, outside, timeout, keeping: Mutex::new(()) }
    }

    /// Start the program with `args`, `input` on its standard input, and wait for it, at most `timeout`.
    fn ask(&self, args: &[&str], input: Option<&Secret>) -> Result<Said, KeychainProblem> {
        self.outside.run(&self.program.to_string_lossy()).map_err(|_| KeychainProblem)?;
        let mut child = Command::new(&self.program)
            .args(args)
            .stdin(if input.is_some() { Stdio::piped() } else { Stdio::null() })
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|_| KeychainProblem)?;
        if let (Some(input), Some(mut stdin)) = (input, child.stdin.take()) {
            // A program that stops reading early is judged by how it ends, and a keep by what it holds after.
            let _ = stdin.write_all(input.expose().as_bytes());
        }
        let mut stdout = child.stdout.take().ok_or(KeychainProblem)?;
        let reading = std::thread::spawn(move || {
            let mut out = Vec::new();
            let _ = stdout.read_to_end(&mut out);
            out
        });
        // Past its time, it is stopped and its reading let go: what it started may still hold the pipe open.
        let status = self.wait(&mut child)?;
        let out = reading.join().map_err(|_| KeychainProblem)?;
        let out = match String::from_utf8(out) {
            Ok(text) => Secret::new(text),
            Err(not_text) => {
                not_text.into_bytes().fill(0);
                return Err(KeychainProblem);
            }
        };
        Ok(Said { status: status.code(), out })
    }

    /// The program's end, or a problem once it has run past `timeout`, when it is stopped.
    fn wait(&self, child: &mut Child) -> Result<ExitStatus, KeychainProblem> {
        let deadline = Instant::now() + self.timeout;
        loop {
            match child.try_wait() {
                Ok(Some(status)) => return Ok(status),
                Ok(None) if Instant::now() < deadline => std::thread::sleep(POLL),
                _ => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(KeychainProblem);
                }
            }
        }
    }

    /// The line `security -i` reads to keep `secret` under `name`, the secret as hex. It is a secret itself.
    fn keep_line(name: &str, secret: &Secret) -> Secret {
        const HEX: &[u8; 16] = b"0123456789abcdef";
        let mut line = String::with_capacity(64 + name.len() + 2 * secret.expose().len());
        line.push_str("add-generic-password -U -s ");
        line.push_str(SERVICE);
        line.push_str(" -a ");
        line.push_str(name);
        line.push_str(" -X ");
        for byte in secret.expose().bytes() {
            line.push(char::from(HEX[usize::from(byte >> 4)]));
            line.push(char::from(HEX[usize::from(byte & 0xf)]));
        }
        line.push('\n');
        Secret::new(line)
    }
}

impl Keychain for LoginKeychain {
    fn find(&self, item: &str) -> Result<Option<Secret>, KeychainProblem> {
        if !keepable_name(item) {
            return Err(KeychainProblem);
        }
        let said = self.ask(&["find-generic-password", "-s", SERVICE, "-a", item, "-w"], None)?;
        match said.status {
            Some(0) => {
                let text = said.out.expose();
                Ok(Some(Secret::new(text.strip_suffix('\n').unwrap_or(text).to_owned())))
            }
            Some(NOT_FOUND) => Ok(None),
            _ => Err(KeychainProblem),
        }
    }

    fn put(&self, item: &str, secret: &Secret) -> Result<(), KeychainProblem> {
        if !keepable_name(item) || !keepable_secret(secret.expose()) {
            return Err(KeychainProblem);
        }
        let _keeping = self.keeping.lock().map_err(|_| KeychainProblem)?;
        // What it says is let go: it can echo the line, and a keep is read back anyway.
        self.ask(&["-i"], Some(&LoginKeychain::keep_line(item, secret)))?;
        match self.find(item)? {
            Some(held) if held.expose() == secret.expose() => Ok(()),
            _ => Err(KeychainProblem),
        }
    }

    fn delete(&self, item: &str) -> Result<bool, KeychainProblem> {
        if !keepable_name(item) {
            return Err(KeychainProblem);
        }
        match self.ask(&["delete-generic-password", "-s", SERVICE, "-a", item], None)?.status {
            Some(0) => Ok(true),
            Some(NOT_FOUND) => Ok(false),
            _ => Err(KeychainProblem),
        }
    }
}
