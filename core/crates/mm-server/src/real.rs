//! The seams as `mailmind-server` runs: the system's clock and nothing between the app and its files, hosts and
//! programs. Its Keychain is the login Keychain (`login_keychain`). Lines of the log go to standard error.

use std::io::Write;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use mm_kernel::log::{Line, Sink};
use mm_kernel::seams::{Clock, Moment, Outside, Tripped};

/// The system's clock.
#[derive(Debug, Default)]
pub struct SystemClock;

impl Clock for SystemClock {
    fn now(&self) -> Moment {
        let since = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default();
        Moment::from_micros(i64::try_from(since.as_micros()).unwrap_or(i64::MAX))
    }
}

/// Lets every file, host and program through.
#[derive(Debug, Default)]
pub struct Unguarded;

impl Outside for Unguarded {
    fn open(&self, _path: &Path) -> Result<(), Tripped> {
        Ok(())
    }

    fn reach(&self, _host: &str) -> Result<(), Tripped> {
        Ok(())
    }

    fn run(&self, _program: &str) -> Result<(), Tripped> {
        Ok(())
    }
}

/// Each line on standard error, as `LEVEL [tag] message`, its fields put in where the message names them and the
/// class of what went wrong after it. A value that looks like an address is withheld.
#[derive(Debug, Default)]
pub struct StandardError;

impl Sink for StandardError {
    fn write(&self, line: &Line<'_>) {
        let mut message = line.message.to_owned();
        for (field, value) in line.fields {
            let shown = if value.contains('@') { "<withheld>" } else { value };
            message = message.replace(&format!("%({})s", field.name()), shown);
        }
        let error = line.error.map(|class| format!(": {class}")).unwrap_or_default();
        let _ = writeln!(std::io::stderr().lock(), "{} [{}] {message}{error}", line.level.name(), line.tag);
    }
}
