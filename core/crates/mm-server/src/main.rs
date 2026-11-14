//! `mailmind-server [port]`: the Rust server on 127.0.0.1 (default port 8721), over the data MAILMIND_HOME names,
//! serving the page `just build-web` built. It makes the database if there is none and upgrades it if its version is
//! behind, then runs until it is stopped. `mailmind-server db check-upgrade` tries the upgrade on a copy first.

use std::process::ExitCode;
use std::sync::Arc;
use std::time::Duration;

use mm_server::environment::{self, Environment};
use mm_server::real::{self, StandardError};
use mm_server::workers::Workers;
use mm_server::{App, Server};

const DEFAULT_PORT: u16 = 8721;

fn main() -> ExitCode {
    mm_kernel::log::install(Box::new(StandardError));
    // `mailmind-server db check-upgrade [--db PATH]`: what the next start's migrations would do, tried on a copy.
    let args: Vec<String> = std::env::args().skip(1).collect();
    if let Some((db, rest)) = args.split_first()
        && db == "db"
    {
        return mm_server::db_command::main(rest);
    }
    let port = match std::env::args().nth(1).map(|p| p.parse::<u16>()) {
        None => DEFAULT_PORT,
        Some(Ok(port)) => port,
        Some(Err(_)) => {
            eprintln!("usage: mailmind-server [port], or mailmind-server db check-upgrade [--db PATH]");
            return ExitCode::from(2);
        }
    };
    let runtime = match tokio::runtime::Builder::new_multi_thread().enable_all().build() {
        Ok(runtime) => runtime,
        Err(_) => {
            eprintln!("mailmind-server: no runtime could start");
            return ExitCode::FAILURE;
        }
    };
    match runtime.block_on(serve(port)) {
        Ok(()) => ExitCode::SUCCESS,
        Err(said) => {
            eprintln!("mailmind-server: {said}");
            ExitCode::FAILURE
        }
    }
}

async fn serve(port: u16) -> Result<(), String> {
    let environment = Environment::of_process();
    let settings = environment.settings()?;
    let seams = real::seams();
    let server = Server::bind(port).await.map_err(|_| format!("can't listen on 127.0.0.1:{port}"))?;
    let port = server.port().map_err(|_| "can't tell which port it listens on")?;
    let dist = environment::repo_root().join("mailmind/http/dist");
    let app = Arc::new(App::assemble(&environment, &settings, dist, port, &seams)?);
    // The background work: the change queue's worker and the jobs, which the served suites never start.
    let workers =
        Workers::start(&app, environment.read_only(), environment.reconcile_secs(), environment.expire_secs());
    eprintln!("MailMind (Rust server) at http://127.0.0.1:{port}");
    // Nothing stops it but the process ending.
    let (_stop, stopped) = tokio::sync::watch::channel(false);
    server.run(app, stopped, Duration::from_secs(3)).await;
    workers.stop();
    Ok(())
}
