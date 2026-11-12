//! `mm-daemon DATABASE [--socket PATH]`: the core as a program, for the command line, the tests and development.
//!
//! It speaks the contract as JSON lines (`mm_app::wire`): over standard input and output, or, with `--socket`, on
//! a Unix socket that only its owner can reach. The database key comes from MAILMIND_DB_KEY, never from an
//! argument, where any process could read it.

#![allow(clippy::print_stdout)]

use std::io::{self, BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::{Arc, Mutex};

use mm_app::wire::{Dispatcher, Outgoing, Sink};
use mm_app::{DbKey, Session, SessionConfig};

fn main() -> ExitCode {
    let mut database = None;
    let mut socket = None;
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--socket" => socket = args.next().map(PathBuf::from),
            _ if database.is_none() => database = Some(PathBuf::from(arg)),
            _ => return usage(),
        }
    }
    let Some(database) = database else {
        return usage();
    };
    let Ok(hex) = std::env::var("MAILMIND_DB_KEY") else {
        eprintln!("mm-daemon: MAILMIND_DB_KEY holds no key");
        return ExitCode::FAILURE;
    };
    let session = DbKey::from_hex(&hex).and_then(|key| Session::open(SessionConfig { database, key, workers: 1 }));
    let session = match session {
        Ok(session) => session,
        Err(refusal) => {
            eprintln!("mm-daemon: {refusal}");
            return ExitCode::FAILURE;
        }
    };
    let served = match socket {
        Some(path) => serve_socket(session, path),
        None => {
            serve(&session, io::stdin().lock(), io::stdout());
            Ok(())
        }
    };
    match served {
        Ok(()) => ExitCode::SUCCESS,
        Err(problem) => {
            eprintln!("mm-daemon: {problem}");
            ExitCode::FAILURE
        }
    }
}

fn usage() -> ExitCode {
    eprintln!("usage: mm-daemon DATABASE [--socket PATH]   (the key in MAILMIND_DB_KEY)");
    ExitCode::from(2)
}

/// Serve one connection until its input ends.
fn serve(session: &Session, input: impl BufRead, output: impl Write + Send + 'static) {
    let output = Arc::new(Mutex::new(output));
    let sink: Sink = Arc::new(move |message: Outgoing| {
        let Ok(mut line) = serde_json::to_vec(&message) else {
            return;
        };
        line.push(b'\n');
        let mut out = output.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        // A shell that has gone away is not an error worth stopping for: its input ends next.
        let _ = out.write_all(&line).and_then(|()| out.flush());
    });
    let dispatcher = Dispatcher::new(session.clone(), sink);
    for line in input.lines() {
        let Ok(line) = line else { break };
        if !line.trim().is_empty() {
            dispatcher.handle_line(&line);
        }
    }
    // The shell has said all it will: send what it asked for before letting go.
    session.settle();
}

#[cfg(unix)]
fn serve_socket(session: Session, path: PathBuf) -> Result<(), String> {
    use std::os::unix::fs::{FileTypeExt, PermissionsExt};
    use std::os::unix::net::UnixListener;

    let folder = path.parent().filter(|p| !p.as_os_str().is_empty()).unwrap_or(std::path::Path::new("."));
    let mode = std::fs::metadata(folder).map_err(|e| format!("the socket's folder: {e}"))?.permissions().mode();
    if mode & 0o077 != 0 {
        return Err("the socket's folder must be its owner's alone (chmod 700)".into());
    }
    if let Ok(found) = std::fs::symlink_metadata(&path) {
        if !found.file_type().is_socket() {
            return Err("something other than a socket is already at that path".into());
        }
        std::fs::remove_file(&path).map_err(|e| format!("the old socket: {e}"))?;
    }
    let listener = UnixListener::bind(&path).map_err(|e| format!("the socket: {e}"))?;
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).map_err(|e| format!("the socket: {e}"))?;
    eprintln!("mm-daemon: listening");
    for stream in listener.incoming() {
        let Ok(stream) = stream else { continue };
        let session = session.clone();
        std::thread::spawn(move || {
            let Ok(reader) = stream.try_clone() else {
                return;
            };
            serve(&session, BufReader::new(reader), stream);
        });
    }
    Ok(())
}

#[cfg(not(unix))]
fn serve_socket(_session: Session, _path: PathBuf) -> Result<(), String> {
    Err("a socket is served on macOS and Linux; on Windows, use standard input and output".into())
}
