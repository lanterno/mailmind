//! The person's keep and block rules, as the classifier reads them: four sets, held once and asked of every message.
//!
//! Reading and storing the rules stays with the app. This is only what a message is matched against: a sender rule
//! names an address exactly, and a domain rule covers the domain and every sub-domain of it.

use std::collections::HashSet;

/// The keep and block lists, each entry as the rules wrote it: the app lower-cases them when it reads them.
#[derive(Debug, Clone, Default)]
pub struct RuleSets {
    keep_senders: HashSet<String>,
    block_senders: HashSet<String>,
    keep_domains: HashSet<String>,
    block_domains: HashSet<String>,
}

impl RuleSets {
    /// The four lists, in the order the rule book names them.
    pub fn new(
        keep_senders: impl IntoIterator<Item = String>,
        block_senders: impl IntoIterator<Item = String>,
        keep_domains: impl IntoIterator<Item = String>,
        block_domains: impl IntoIterator<Item = String>,
    ) -> Self {
        Self {
            keep_senders: keep_senders.into_iter().collect(),
            block_senders: block_senders.into_iter().collect(),
            keep_domains: keep_domains.into_iter().collect(),
            block_domains: block_domains.into_iter().collect(),
        }
    }

    /// A keep rule names this address.
    pub fn keeps_sender(&self, addr: &str) -> bool {
        self.keep_senders.contains(addr)
    }

    /// A block rule names this address.
    pub fn blocks_sender(&self, addr: &str) -> bool {
        self.block_senders.contains(addr)
    }

    /// A keep rule covers this domain.
    pub fn keeps_domain(&self, domain: &str) -> bool {
        domain_listed(domain, &self.keep_domains)
    }

    /// A block rule covers this domain.
    pub fn blocks_domain(&self, domain: &str) -> bool {
        domain_listed(domain, &self.block_domains)
    }
}

/// `domain` is `entry` or one of its sub-domains, and no lookalike: `mail.example.test` is covered by
/// `example.test`, and `badexample.test` isn't.
pub fn domain_covers(domain: &str, entry: &str) -> bool {
    domain == entry || domain.strip_suffix(entry).is_some_and(|rest| rest.ends_with('.'))
}

/// Whether an entry of `listed` covers `domain`. The entries that could are the domain itself and what follows each
/// of its dots, so this asks the set for those, however many entries it holds.
pub fn domain_listed(domain: &str, listed: &HashSet<String>) -> bool {
    listed.contains(domain) || domain.match_indices('.').any(|(dot, _)| listed.contains(&domain[dot + 1..]))
}
