//! The login Keychain as the tests have it (tests/support/fake_keychain.py): MailMind's items as a JSON object of
//! name to secret, in the file MAILMIND_TEST_KEYCHAIN names, which the suite and the server share. With no file named
//! it holds nothing and keeps nothing. It stands in the seam the login Keychain fills.

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use mm_kernel::seams::{Keychain, KeychainProblem, Outside, Secret, keepable_name, keepable_secret};
use serde_json::{Map, Value};

#[derive(Debug)]
pub struct FakeKeychain {
    file: Option<PathBuf>,
    outside: Arc<dyn Outside>,
    /// A change and the file it writes, one at a time.
    changing: Mutex<()>,
}

impl FakeKeychain {
    pub fn new(file: Option<PathBuf>, outside: Arc<dyn Outside>) -> FakeKeychain {
        FakeKeychain { file, outside, changing: Mutex::new(()) }
    }

    /// What the file holds: nothing when there is no file yet.
    fn held(&self, file: &Path) -> Result<Map<String, Value>, KeychainProblem> {
        self.outside.open(file).map_err(|_| KeychainProblem)?;
        let text = match std::fs::read(file) {
            Ok(text) => text,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Map::new()),
            Err(_) => return Err(KeychainProblem),
        };
        match serde_json::from_slice(&text).map_err(|_| KeychainProblem)? {
            Value::Object(held) => Ok(held),
            _ => Err(KeychainProblem),
        }
    }

    /// `held` in place of what the file held, as fake_keychain.py writes it: beside it first, readable by its owner.
    fn save(&self, file: &Path, held: Map<String, Value>) -> Result<(), KeychainProblem> {
        let mut beside = file.as_os_str().to_owned();
        beside.push(".tmp");
        let beside = PathBuf::from(beside);
        self.outside.open(&beside).map_err(|_| KeychainProblem)?;
        let text = serde_json::to_vec(&Value::Object(held)).map_err(|_| KeychainProblem)?;
        let mut options = std::fs::OpenOptions::new();
        options.write(true).create(true).truncate(true);
        #[cfg(unix)]
        std::os::unix::fs::OpenOptionsExt::mode(&mut options, 0o600);
        let written = options.open(&beside).and_then(|mut f| std::io::Write::write_all(&mut f, &text));
        written.and_then(|()| std::fs::rename(&beside, file)).map_err(|_| KeychainProblem)
    }

    /// Change what the file holds by `change`, which says whether it changed anything and what to answer.
    fn change<T>(&self, change: impl FnOnce(&mut Map<String, Value>) -> (bool, T)) -> Result<T, KeychainProblem> {
        let Some(file) = &self.file else { return Err(KeychainProblem) };
        let _changing = self.changing.lock().map_err(|_| KeychainProblem)?;
        let mut held = self.held(file)?;
        let (changed, answer) = change(&mut held);
        if changed {
            self.save(file, held)?;
        }
        Ok(answer)
    }
}

impl Keychain for FakeKeychain {
    fn find(&self, item: &str) -> Result<Option<Secret>, KeychainProblem> {
        let Some(file) = &self.file else { return Ok(None) };
        Ok(self.held(file)?.get(item).and_then(Value::as_str).map(|s| Secret::new(s.to_owned())))
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
