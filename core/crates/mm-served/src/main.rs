//! The Rust server as the API suite (tests/api) and the browser suite run it: mm-server, behind the line protocol
//! tests/support/serve_python.py documents, with a frozen clock, a tripwire and the fake Keychain. Never shipped.
//!
//! It reads, beside MAILMIND_HOME and MAILMIND_DB_KEY: MAILMIND_TEST_NOW (where the clock stands), MAILMIND_TEST_DIST
//! (the build it serves), MAILMIND_TEST_PORT (the port, when the suite restarts it), MAILMIND_TEST_GUARDED (folders
//! the tripwire guards besides the repo root) and MAILMIND_TEST_KEYCHAIN (the fake Keychain's file).
//!
//! It writes `ready <port>` on standard output once it answers, then answers each command on standard input with one
//! line: `clock <ISO 8601>`, `work <job> <JSON>`, `settle`, `check`; at the end of its input it stops. Standard output
//! is the protocol's alone: the server's log goes to standard error, which the suite keeps.

use std::io::{BufRead, Write};
use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::Arc;
use std::time::Duration;

use mm_server::environment::{Environment, repo_root};
use mm_server::real::StandardError;
use mm_server::{App, Seams, Server};
use serde_json::Value;

use mm_served::clock::{FrozenClock, parse_iso};
use mm_served::keychain::FakeKeychain;
use mm_served::tripwire::Tripwire;

/// What `settle` waits for the requests being answered, at most.
const SETTLE: Duration = Duration::from_secs(30);

fn say(line: &str) {
    let mut out = std::io::stdout().lock();
    let _ = writeln!(out, "{line}");
    let _ = out.flush();
}

fn main() -> ExitCode {
    mm_kernel::log::install(Box::new(StandardError));
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(said) => {
            eprintln!("mm-served: {said}");
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<(), String> {
    let environment = Environment::of_process();
    let settings = environment.settings()?;
    let now = environment.raw("MAILMIND_TEST_NOW").and_then(parse_iso).ok_or("MAILMIND_TEST_NOW isn't a time")?;
    let dist = PathBuf::from(environment.raw("MAILMIND_TEST_DIST").ok_or("MAILMIND_TEST_DIST isn't set")?);
    let port = match environment.raw("MAILMIND_TEST_PORT").filter(|p| !p.is_empty()) {
        Some(port) => port.parse::<u16>().map_err(|_| "MAILMIND_TEST_PORT isn't a port")?,
        None => 0,
    };
    let guarded: Vec<PathBuf> = environment
        .raw("MAILMIND_TEST_GUARDED")
        .unwrap_or_default()
        .split(':')
        .filter(|f| !f.is_empty())
        .map(PathBuf::from)
        .collect();
    let tripwire = Arc::new(Tripwire::new(&repo_root(), &guarded, &[settings.home.clone(), dist.clone()]));
    let clock = Arc::new(FrozenClock::at(now));
    let keychain = FakeKeychain::new(environment.raw("MAILMIND_TEST_KEYCHAIN").map(PathBuf::from), tripwire.clone());
    let seams = Seams { clock: clock.clone(), outside: tripwire.clone(), keychain: Arc::new(keychain) };

    let runtime = tokio::runtime::Builder::new_multi_thread().enable_all().build().map_err(|_| "no runtime")?;
    let server = runtime.block_on(Server::bind(port)).map_err(|_| format!("can't listen on 127.0.0.1:{port}"))?;
    let port = server.port().map_err(|_| "can't tell which port it listens on")?;
    let app = Arc::new(App::assemble(&environment, &settings, dist, port, &seams)?);
    let (stop, stopped) = tokio::sync::watch::channel(false);
    let serving = runtime.spawn(server.run(app.clone(), stopped, Duration::from_secs(3)));
    say(&format!("ready {port}"));

    for line in std::io::stdin().lock().lines() {
        let Ok(line) = line else { break };
        let (command, argument) = line.trim().split_once(' ').unwrap_or((line.trim(), ""));
        let answer = match command {
            "clock" => match parse_iso(argument) {
                Some(moment) => {
                    clock.move_to(moment);
                    "ok".to_owned()
                }
                None => "failed clock: not a time".to_owned(),
            },
            "work" => "failed not on the Rust server yet".to_owned(),
            "settle" => match app.work.settle(SETTLE) {
                0 => "ok".to_owned(),
                n => format!("busy {n} request(s) still being answered"),
            },
            "check" => {
                let found = tripwire.take();
                if found.is_empty() {
                    "ok".to_owned()
                } else {
                    let listed = Value::Array(found.into_iter().map(Value::String).collect());
                    format!("problems {}", mm_server::json::dumps(&listed))
                }
            }
            other => format!("unknown command {other:?}"),
        };
        say(&answer);
    }
    let _ = stop.send(true);
    let _ = runtime.block_on(serving);
    // A request still waiting on a mail server is cut off, as uvicorn cancels one past its grace.
    runtime.shutdown_timeout(Duration::from_secs(1));
    Ok(())
}
