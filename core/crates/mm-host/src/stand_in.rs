//! What a demo or test home runs on in place of the world: the login Keychain as a file, as the tests' `fake_keychain`
//! keeps it (a JSON object of item to secret, readable by its owner only), and an `Outside` that reaches no host but
//! this machine and runs no program. A home the shell gives no stand-in runs on the real seams (`real::seams`).

use std::path::{Path, PathBuf};
use std::sync::Mutex;

use mm_kernel::seams::{Keychain, KeychainProblem, Outside, Secret, Tripped, keepable_name, keepable_secret};
use serde_json::{Map, Value};

/// The login Keychain as a file: what `fake_keychain.py` writes, so the demo that starts the app and the app share it.
#[derive(Debug)]
pub struct FileKeychain {
    file: PathBuf,
    /// A change and the file it writes, one at a time.
    changing: Mutex<()>,
}

impl FileKeychain {
    pub fn new(file: PathBuf) -> FileKeychain {
        FileKeychain { file, changing: Mutex::new(()) }
    }

    /// What the file holds: nothing when there is no file yet.
    fn held(&self) -> Result<Map<String, Value>, KeychainProblem> {
        let text = match std::fs::read(&self.file) {
            Ok(text) => text,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Map::new()),
            Err(_) => return Err(KeychainProblem),
        };
        match serde_json::from_slice(&text).map_err(|_| KeychainProblem)? {
            Value::Object(held) => Ok(held),
            _ => Err(KeychainProblem),
        }
    }

    /// `held` in place of what the file held: written beside it first, readable by its owner, then moved over it.
    fn save(&self, held: Map<String, Value>) -> Result<(), KeychainProblem> {
        let mut beside = self.file.as_os_str().to_owned();
        beside.push(".tmp");
        let beside = PathBuf::from(beside);
        let text = serde_json::to_vec(&Value::Object(held)).map_err(|_| KeychainProblem)?;
        let mut options = std::fs::OpenOptions::new();
        options.write(true).create(true).truncate(true);
        #[cfg(unix)]
        std::os::unix::fs::OpenOptionsExt::mode(&mut options, 0o600);
        let written = options.open(&beside).and_then(|mut f| std::io::Write::write_all(&mut f, &text));
        written.and_then(|()| std::fs::rename(&beside, &self.file)).map_err(|_| KeychainProblem)
    }

    /// Change what the file holds by `change`, which says whether it changed anything and what to answer.
    fn change<T>(&self, change: impl FnOnce(&mut Map<String, Value>) -> (bool, T)) -> Result<T, KeychainProblem> {
        let _changing = self.changing.lock().map_err(|_| KeychainProblem)?;
        let mut held = self.held()?;
        let (changed, answer) = change(&mut held);
        if changed {
            self.save(held)?;
        }
        Ok(answer)
    }
}

impl Keychain for FileKeychain {
    fn find(&self, item: &str) -> Result<Option<Secret>, KeychainProblem> {
        Ok(self.held()?.get(item).and_then(Value::as_str).map(|s| Secret::new(s.to_owned())))
    }

    fn put(&self, item: &str, secret: &Secret) -> Result<(), KeychainProblem> {
        if !keepable_name(item) || !keepable_secret(secret.expose()) {
            return Err(KeychainProblem);
        }
        self.change(|held| {
            held.insert(item.to_owned(), Value::String(secret.expose().to_owned()));
            (true, ())
        })
    }

    fn delete(&self, item: &str) -> Result<bool, KeychainProblem> {
        if !keepable_name(item) {
            return Err(KeychainProblem);
        }
        self.change(|held| {
            let had = held.remove(item).is_some();
            (had, had)
        })
    }
}

/// A demo or test home's way out: any file, but no host off this machine and no program, so nothing it does reaches
/// a real mail server, the Claude API, 1Password or the login Keychain.
#[derive(Debug, Default)]
pub struct LoopbackOnly;

/// Whether `host` names this machine.
pub fn is_loopback(host: &str) -> bool {
    let host = host.trim_start_matches('[').trim_end_matches(']');
    host.eq_ignore_ascii_case("localhost")
        || host.parse::<std::net::IpAddr>().is_ok_and(|address| address.is_loopback())
}

impl Outside for LoopbackOnly {
    fn open(&self, _path: &Path) -> Result<(), Tripped> {
        Ok(())
    }

    fn reach(&self, host: &str) -> Result<(), Tripped> {
        if is_loopback(host) { Ok(()) } else { Err(Tripped("a host off this machine".into())) }
    }

    fn run(&self, _program: &str) -> Result<(), Tripped> {
        Err(Tripped("a program".into()))
    }
}
