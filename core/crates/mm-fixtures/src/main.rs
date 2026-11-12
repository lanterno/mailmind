//! `mm-fixtures OUT [MESSAGES] [SEED]`: make a fictional mailbox at OUT, keyed with MAILMIND_DB_KEY when it is
//! set and with the fixture key when it is not.

use std::path::PathBuf;
use std::process::ExitCode;
use std::time::Instant;

use mm_fixtures::{BUDGET_MESSAGES, FIXTURE_KEY_HEX, SEED};
use mm_store::DbKey;

fn main() -> ExitCode {
    let mut args = std::env::args().skip(1);
    let Some(out) = args.next().map(PathBuf::from) else {
        eprintln!("usage: mm-fixtures OUT [MESSAGES] [SEED]");
        return ExitCode::from(2);
    };
    let count = args.next().and_then(|n| n.parse().ok()).unwrap_or(BUDGET_MESSAGES);
    let seed = args.next().and_then(|n| n.parse().ok()).unwrap_or(SEED);
    let key = match std::env::var("MAILMIND_DB_KEY") {
        Ok(hex) => DbKey::from_hex(&hex),
        Err(_) => DbKey::from_hex(FIXTURE_KEY_HEX),
    };
    let started = Instant::now();
    match key.and_then(|key| mm_fixtures::build(&out, &key, count, seed)) {
        Ok(added) => {
            eprintln!("{added} fictional messages in {:.1} s", started.elapsed().as_secs_f64());
            ExitCode::SUCCESS
        }
        Err(refusal) => {
            eprintln!("mm-fixtures: {refusal}");
            ExitCode::FAILURE
        }
    }
}
