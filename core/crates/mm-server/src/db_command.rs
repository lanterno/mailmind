//! `mailmind-server db check-upgrade [--db PATH]`: what the next start's migrations would do to the database, tried
//! on a copy (`mm_store::upgrade_check`), as `python -m mailmind db check-upgrade` does. The database is the data
//! directory's (MAILMIND_HOME), unless `--db` names another, and its key MAILMIND_DB_KEY's, or the Keychain's, as the
//! server reads it; none is ever made. It prints only table names, row counts and verdicts, and exits 0 when every
//! check passed, 1 when one didn't, and 2 when there was nothing it could check.

use std::io::Write;
use std::path::PathBuf;
use std::process::ExitCode;

use mm_store::Database;
use mm_store::upgrade_check::{self, Verdict};

use crate::app::Seams;
use crate::environment::Environment;

const USAGE: &str = "usage: mailmind-server db check-upgrade [--db PATH]";

/// `mailmind-server db …` as the process runs it: `args` are what follow `db`.
pub fn main(args: &[String]) -> ExitCode {
    let mut out = |line: &str| {
        let mut stdout = std::io::stdout().lock();
        let _ = writeln!(stdout, "{line}");
    };
    match run(args, &Environment::of_process(), &crate::real::seams(), &mut out) {
        Ok(verdict) => ExitCode::from(verdict.code()),
        Err(said) => {
            eprintln!("mailmind-server: {said}");
            ExitCode::from(Verdict::CannotRun.code())
        }
    }
}

/// Run `db` with `args` over `environment` and `seams`, its report said through `say`. What stops it before it can
/// check anything is the error.
pub fn run(
    args: &[String],
    environment: &Environment,
    seams: &Seams,
    say: &mut dyn FnMut(&str),
) -> Result<Verdict, String> {
    let path = match args {
        [command] if command == "check-upgrade" => environment.settings()?.db(),
        [command, flag, path] if command == "check-upgrade" && flag == "--db" => PathBuf::from(path),
        _ => return Err(USAGE.into()),
    };
    // Before the key is looked for, as the Python app's does: with no database, there is nothing to open.
    if seams.outside.open(&path).is_err() || !path.is_file() {
        say("No database to check.");
        return Ok(Verdict::CannotRun);
    }
    let key = crate::database_version::existing_key(environment, seams)?;
    let database = Database::new(path, key, seams.outside.clone());
    Ok(upgrade_check::check_upgrade(&database, &*seams.clock, say))
}
