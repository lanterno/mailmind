//! A domain's mail servers, as Settings asks of an address's domain (`mailmind.accounts.adapters.mail_hosts`): its
//! MX records, asked of the nameservers `/etc/resolv.conf` lists (`System`) or of the ones a test names (`asking`),
//! over `mm_net::dns`. `Outside` is asked before the file is read and before each nameserver is reached, so a
//! nameserver a test may not reach is never asked. A lookup that doesn't work names no hosts, and says nothing.

use std::net::SocketAddr;
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use mm_accounts::ports::MailHosts;
use mm_kernel::seams::Outside;
use mm_net::dns;

/// The file the system lists its nameservers in.
pub const RESOLV_CONF: &str = "/etc/resolv.conf";
/// How long a nameserver has to answer before the next is asked.
pub const TIMEOUT: Duration = Duration::from_secs(2);
/// The most nameservers asked in one lookup.
pub const MOST_ASKED: usize = 3;

/// Where the nameservers come from.
#[derive(Debug)]
enum Nameservers {
    /// The system's, as `/etc/resolv.conf` lists them when a lookup is made.
    System,
    /// These, and no others.
    These(Vec<SocketAddr>),
}

/// MX lookups over UDP, each nameserver asked of `Outside` first.
#[derive(Debug)]
pub struct SystemMailHosts {
    outside: Arc<dyn Outside>,
    nameservers: Nameservers,
}

impl SystemMailHosts {
    /// The system's nameservers.
    pub fn new(outside: Arc<dyn Outside>) -> SystemMailHosts {
        SystemMailHosts { outside, nameservers: Nameservers::System }
    }

    /// `nameservers`, and no others: none asks none.
    pub fn asking(outside: Arc<dyn Outside>, nameservers: Vec<SocketAddr>) -> SystemMailHosts {
        SystemMailHosts { outside, nameservers: Nameservers::These(nameservers) }
    }

    fn nameservers(&self) -> Vec<SocketAddr> {
        match &self.nameservers {
            Nameservers::These(these) => these.clone(),
            Nameservers::System => {
                let path = Path::new(RESOLV_CONF);
                if self.outside.open(path).is_err() {
                    return Vec::new();
                }
                std::fs::read_to_string(path).map(|text| dns::nameservers(&text)).unwrap_or_default()
            }
        }
    }
}

impl MailHosts for SystemMailHosts {
    fn of(&self, domain: &str) -> Vec<String> {
        let reachable: Vec<SocketAddr> = self
            .nameservers()
            .into_iter()
            .take(MOST_ASKED)
            .filter(|nameserver| self.outside.reach(&nameserver.ip().to_string()).is_ok())
            .collect();
        if reachable.is_empty() {
            return Vec::new();
        }
        let mut drawn = [0u8; 2];
        if getrandom::fill(&mut drawn).is_err() {
            return Vec::new();
        }
        dns::mail_hosts(&reachable, domain, u16::from_be_bytes(drawn), TIMEOUT)
    }
}

/// Asks no nameserver, and names no hosts: a demo's and a test's, which reach no network.
#[derive(Debug, Default)]
pub struct NoLookups;

impl MailHosts for NoLookups {
    fn of(&self, _domain: &str) -> Vec<String> {
        Vec::new()
    }
}
