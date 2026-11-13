//! `mailmind-server [port]`: the Rust server on 127.0.0.1 (default port 8721), over the data MAILMIND_HOME names,
//! serving the page `just build-web` built. It runs until it is stopped.

use std::process::ExitCode;
use std::sync::Arc;
use std::time::Duration;

use mm_server::environment::{self, Environment};
use mm_server::real::{NoKeychain, StandardError, SystemClock, Unguarded};
use mm_server::{App, Seams, Server};

const DEFAULT_PORT: u16 = 8721;

fn main() -> ExitCode {
    mm_kernel::log::install(Box::new(StandardError));
    let port = match std::env::args().nth(1).map(|p| p.parse::<u16>()) {
        None => DEFAULT_PORT,
        Some(Ok(port)) => port,
        Some(Err(_)) => {
            eprintln!("usage: mailmind-server [port]");
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
    let seams = Seams { clock: Arc::new(SystemClock), outside: Arc::new(Unguarded), keychain: Arc::new(NoKeychain) };
    let server = Server::bind(port).await.map_err(|_| format!("can't listen on 127.0.0.1:{port}"))?;
    let port = server.port().map_err(|_| "can't tell which port it listens on")?;
    let dist = environment::repo_root().join("mailmind/http/dist");
    let app = App::assemble(&environment, &settings, dist, port, &seams)?;
    eprintln!("MailMind (Rust server) at http://127.0.0.1:{port}");
    // Nothing stops it but the process ending.
    let (_stop, stopped) = tokio::sync::watch::channel(false);
    server.run(Arc::new(app), stopped, Duration::from_secs(3)).await;
    Ok(())
}
