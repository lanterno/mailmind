//! The model servers on this Mac, as the finder asks them (`mailmind.assistant.adapters.local_servers`, #49): each
//! one's `GET {url}/models` over `mm-openai`, all at once, a second each, with no key. A URL off this Mac is never
//! reached, and `Outside` is asked before each one that is. The shipped server asks the servers MailMind knows of
//! (`local_servers::KNOWN`); the served suites name their own stand-ins in MAILMIND_TEST_LOCAL_AI, which `mm-served`
//! hands over here (`at`), and a demo's and a test's home ask none, so no test ever probes a model server the machine
//! is running.

use std::sync::Arc;
use std::time::Duration;

use mm_assistant::local_servers::KNOWN;
use mm_assistant::ports::{LocalServer, LocalServers};
use mm_kernel::ai_provider::{AiProvider, on_this_machine};
use mm_kernel::seams::Outside;
use mm_openai::ChatApi;

/// How long a server has to answer.
pub const TIMEOUT: Duration = Duration::from_secs(1);

/// The servers asked, by name and URL, each over HTTP on this Mac.
#[derive(Debug)]
pub struct LocalModelServers {
    outside: Arc<dyn Outside>,
    servers: Vec<(String, String)>,
}

impl LocalModelServers {
    /// The servers MailMind knows of, at their usual addresses.
    pub fn known(outside: Arc<dyn Outside>) -> LocalModelServers {
        let servers = KNOWN.iter().map(|(name, url)| ((*name).to_owned(), (*url).to_owned())).collect();
        LocalModelServers { outside, servers }
    }

    /// These servers, and no others: none asks none.
    pub fn at(outside: Arc<dyn Outside>, servers: Vec<(String, String)>) -> LocalModelServers {
        LocalModelServers { outside, servers }
    }

    /// The servers a test names, as MAILMIND_TEST_LOCAL_AI says them: `name=url` pairs separated by `;`, a blank
    /// pair left out. None when a pair names no URL.
    pub fn listed(said: &str) -> Option<Vec<(String, String)>> {
        said.split(';')
            .map(str::trim)
            .filter(|pair| !pair.is_empty())
            .map(|pair| {
                let (name, url) = pair.split_once('=')?;
                let (name, url) = (name.trim(), url.trim());
                (!url.is_empty()).then(|| (name.to_owned(), url.to_owned()))
            })
            .collect()
    }

    /// The ids `url`'s list of models names, or None where it answered none in time, or isn't on this Mac.
    fn models(&self, url: &str) -> Option<Vec<String>> {
        if !on_this_machine(url) {
            return None;
        }
        let api = ChatApi::new(AiProvider::Local, url, None, self.outside.clone()).with_limits(TIMEOUT, 0);
        api.readable_models().ok()
    }
}

impl LocalServers for LocalModelServers {
    fn ask(&self) -> Vec<LocalServer> {
        std::thread::scope(|scope| {
            let asking: Vec<_> =
                self.servers.iter().map(|(name, url)| (name, url, scope.spawn(move || self.models(url)))).collect();
            asking
                .into_iter()
                .map(|(name, url, asked)| LocalServer {
                    name: name.clone(),
                    url: url.clone(),
                    models: asked.join().ok().flatten(),
                })
                .collect()
        })
    }
}

/// Asks no server, and finds none: a demo's and a test's, which reach no model server.
#[derive(Debug, Default)]
pub struct NoLocalServers;

impl LocalServers for NoLocalServers {
    fn ask(&self) -> Vec<LocalServer> {
        Vec::new()
    }
}
