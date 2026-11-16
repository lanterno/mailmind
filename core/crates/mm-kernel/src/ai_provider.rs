//! The AI providers MailMind can ask (`mailmind.kernel.ai_provider`, #49): a closed set, which the preferences choose
//! from, the Keychain keeps a key for, and the Assistant reaches; and the address a person may give a provider's API
//! at.
//!
//! Claude is the default, so a file that says nothing goes on as it did. Anthropic's API is its own; every other
//! provider speaks OpenAI's Chat Completions: `local` a model server on this Mac (Ollama's address unless another is
//! given), and `compatible` any service that speaks it, at the address the person gives. Every one may keep a key.

/// One of the providers, in the order a list of them is given.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum AiProvider {
    Anthropic,
    OpenAi,
    Gemini,
    /// A model server on this Mac: Ollama, LM Studio, llama.cpp, vLLM, Jan, GPT4All.
    Local,
    Compatible,
}

impl AiProvider {
    /// Every provider, in the closed set's order.
    pub const ALL: [AiProvider; 5] =
        [AiProvider::Anthropic, AiProvider::OpenAi, AiProvider::Gemini, AiProvider::Local, AiProvider::Compatible];
    /// What a file that names none chooses.
    pub const DEFAULT: AiProvider = AiProvider::Anthropic;
    /// Each one's id, in the same order.
    pub const IDS: [&'static str; 5] = ["anthropic", "openai", "gemini", "local", "compatible"];

    /// Its id, as the preferences and the API name it.
    pub fn id(self) -> &'static str {
        match self {
            AiProvider::Anthropic => "anthropic",
            AiProvider::OpenAi => "openai",
            AiProvider::Gemini => "gemini",
            AiProvider::Local => "local",
            AiProvider::Compatible => "compatible",
        }
    }

    /// The provider an id names, or None for one that is none.
    pub fn parse(id: &str) -> Option<AiProvider> {
        AiProvider::ALL.into_iter().find(|p| p.id() == id)
    }

    /// What a client calls it.
    pub fn label(self) -> &'static str {
        match self {
            AiProvider::Anthropic => "Claude (Anthropic)",
            AiProvider::OpenAi => "OpenAI",
            AiProvider::Gemini => "Google Gemini",
            AiProvider::Local => "A model on this Mac",
            AiProvider::Compatible => "Another OpenAI-compatible service",
        }
    }

    /// Whether AI is off with no key for it: Claude, OpenAI and Gemini. A model on this Mac and a compatible service
    /// may have one, and need none.
    pub fn needs_key(self) -> bool {
        matches!(self, AiProvider::Anthropic | AiProvider::OpenAi | AiProvider::Gemini)
    }
}

/// What a key typed or pasted with a space or a line break inside it is told: no provider's key holds one.
pub const KEY_SPACED: &str = "An API key can't hold spaces or line breaks.";

/// Whether a key holds what no provider's key does, and no request header can carry: a space (any `str.isspace`
/// one) or a control character. A key typed or pasted so is refused in `KEY_SPACED`'s words; what may be sent is
/// `sendable`'s to say.
pub fn key_spaced(key: &str) -> bool {
    key.chars().any(|c| crate::text::is_space(c) || c.is_control())
}

/// Whether a key may be sent (`providers.sendable`): every character printable ASCII without a space, `!` to `~`, as
/// every provider's keys are. Any other key, from anywhere, is a refused key, never sent: one with a space or a line
/// break was pasted or read wrong, and no request header carries one outside ASCII.
pub fn sendable(key: &str) -> bool {
    key.chars().all(|c| ('!'..='~').contains(&c))
}

/// `text` with `key` taken out of it, as it is and as JSON writes it in a string (`providers.unkeyed`), so what a service
/// said, words or compact JSON, quotes no key back. A key sent is printable ASCII, whose only characters JSON escapes in a
/// string are `"` and `\`.
pub fn unkeyed(text: &str, key: &str) -> String {
    if key.is_empty() {
        return text.to_owned();
    }
    let escaped = key.replace('\\', "\\\\").replace('"', "\\\"");
    let text = text.replace(&escaped, "<key>"); // the longer, escaped form first
    if escaped == key { text } else { text.replace(key, "<key>") }
}

/// The longest base URL a person may give, in characters.
pub const BASE_URL_MOST: usize = 500;

/// The host a URL in printable ASCII names, its scheme `https` or `http` in any case, with no user or password, no
/// query and no fragment, and a port from 1 to 65535 when it names one, a `:` naming none: None for any other URL, or
/// text that is none.
fn host_of(url: &str) -> Option<(bool, String)> {
    if url.chars().any(|c| !('!'..='~').contains(&c)) || url.contains(['?', '#']) {
        return None;
    }
    let (scheme, rest) = url.split_once("://")?;
    let tls = match scheme.to_ascii_lowercase().as_str() {
        "https" => true,
        "http" => false,
        _ => return None,
    };
    let authority = rest.split('/').next().unwrap_or_default();
    if authority.contains('@') {
        return None;
    }
    let (host, port) = match authority.strip_prefix('[') {
        Some(bracketed) => {
            let (host, after) = bracketed.split_once(']')?;
            host.parse::<std::net::Ipv6Addr>().ok()?;
            match after {
                "" => (host, None),
                _ => (host, Some(after.strip_prefix(':')?)),
            }
        }
        None => match authority.split_once(':') {
            Some((host, port)) => (host, Some(port)),
            None => (authority, None),
        },
    };
    // An empty port is malformed, as is port 0: no HTTP client reaches a service at either.
    let a_port = |p: &str| p.bytes().all(|b| b.is_ascii_digit()) && p.parse::<u16>().is_ok_and(|n| n > 0);
    if port.is_some_and(|p| !a_port(p)) {
        return None;
    }
    if host.is_empty() || host.contains(['[', ']']) {
        return None;
    }
    Some((tls, host.to_ascii_lowercase()))
}

/// Whether a host is this machine as a base URL may name it over plain HTTP: `127.0.0.1`, `::1` or `localhost`,
/// written just so, as the Python app reads it.
fn loopback(host: &str) -> bool {
    LOOPBACK.contains(&host)
}

/// The hosts that name this machine.
const LOOPBACK: [&str; 3] = ["127.0.0.1", "::1", "localhost"];

/// A base URL as a person may give one, its trailing `/` dropped: at most `BASE_URL_MOST` characters of printable
/// ASCII, an `https` URL with a host, or an `http` one whose host is this machine (`127.0.0.1`, `::1`, `localhost`),
/// with no user or password, no query and no fragment, and any port it names from 1 to 65535. None for any other.
pub fn base_url(given: &str) -> Option<String> {
    if given.chars().count() > BASE_URL_MOST {
        return None;
    }
    let (tls, host) = host_of(given)?;
    (tls || loopback(&host)).then(|| given.trim_end_matches('/').to_owned())
}

/// Whether a base URL names this machine (`127.0.0.1`, `::1` or `localhost`), over HTTP or HTTPS.
pub fn on_this_machine(url: &str) -> bool {
    host_of(url).is_some_and(|(_, host)| loopback(&host))
}
