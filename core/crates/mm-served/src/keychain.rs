//! The login Keychain as the tests have it (tests/support/fake_keychain.py): MailMind's items as a JSON object of
//! name to secret, in the file MAILMIND_TEST_KEYCHAIN names, which the suite and the server share. With no file named
//! it holds nothing. It stands in the seam the real Keychain will fill.

use std::path::PathBuf;
use std::sync::Arc;

use mm_kernel::seams::{Keychain, KeychainProblem, Outside, Secret};
use serde_json::Value;

#[derive(Debug)]
pub struct FakeKeychain {
    file: Option<PathBuf>,
    outside: Arc<dyn Outside>,
}

impl FakeKeychain {
    pub fn new(file: Option<PathBuf>, outside: Arc<dyn Outside>) -> FakeKeychain {
        FakeKeychain { file, outside }
    }
}

impl Keychain for FakeKeychain {
    fn find(&self, item: &str) -> Result<Option<Secret>, KeychainProblem> {
        let Some(file) = &self.file else { return Ok(None) };
        self.outside.open(file).map_err(|_| KeychainProblem)?;
        let text = match std::fs::read(file) {
            Ok(text) => text,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(_) => return Err(KeychainProblem),
        };
        let held: Value = serde_json::from_slice(&text).map_err(|_| KeychainProblem)?;
        Ok(held.get(item).and_then(Value::as_str).map(|s| Secret::new(s.to_owned())))
    }
}
