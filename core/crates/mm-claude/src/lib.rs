//! The network: the Claude API's Messages endpoint (`assistant.adapters.anthropic_sdk`), as the Assistant's
//! `LanguageModel`, on mm-net.
//!
//! A request goes over HTTPS, its certificate verified on the roots the system trusts. Plain HTTP goes only to a
//! loopback host, which is what a stand-in for the API is: the key is never sent in the clear to anything off this
//! machine. The host is asked of `Outside` before a connection is made. A call is sent again, twice, after a status
//! worth sending again (`RETRIED`) or a lost connection, waiting what `retry-after` says or else a little longer
//! each time; any other status is a `ModelError` saying it. The key never reaches a log line or an error.

pub mod http;
pub mod wire;

use std::net::IpAddr;
use std::sync::Arc;
use std::time::Duration;

use mm_assistant::models::FALLBACK_BETA;
use mm_assistant::ports::{LanguageModel, ModelError, Prompt, RETRIED, Reply};
use mm_kernel::seams::{Outside, Secret};
use mm_net::{Lost, Stream};

/// Where API calls go unless ANTHROPIC_BASE_URL names another.
pub const DEFAULT_URL: &str = "https://api.anthropic.com";
/// The API version every request names.
pub const VERSION: &str = "2026-10-21";
/// Seconds per attempt.
pub const TIMEOUT: Duration = Duration::from_secs(120);
/// Attempts after the first.
pub const RETRIES: u32 = 2;

/// Where the API is: its scheme, host, port and the path its routes hang from.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Endpoint {
    pub tls: bool,
    pub host: String,
    pub port: u16,
    /// The path before `/v1/messages`, without a trailing `/`.
    pub base: String,
}

impl Endpoint {
    /// An `http://` or `https://` URL as an endpoint, or None for anything else.
    pub fn parse(url: &str) -> Option<Endpoint> {
        let url = url.trim();
        let (tls, rest) = match url.strip_prefix("https://") {
            Some(rest) => (true, rest),
            None => (false, url.strip_prefix("http://")?),
        };
        let (authority, path) = rest.split_at(rest.find('/').unwrap_or(rest.len()));
        if authority.is_empty() || authority.contains('@') {
            return None;
        }
        let default_port = if tls { 443 } else { 80 };
        let (host, port) = if let Some(bracketed) = authority.strip_prefix('[') {
            let (host, after) = bracketed.split_once(']')?;
            let port = match after.strip_prefix(':') {
                Some(port) => port.parse().ok()?,
                None if after.is_empty() => default_port,
                None => return None,
            };
            (host.to_owned(), port)
        } else {
            match authority.rsplit_once(':') {
                Some((host, port)) => (host.to_owned(), port.parse().ok()?),
                None => (authority.to_owned(), default_port),
            }
        };
        if host.is_empty() {
            return None;
        }
        let path = path.split(['?', '#']).next().unwrap_or_default();
        Some(Endpoint { tls, host, port, base: path.trim_end_matches('/').to_owned() })
    }

    /// Whether the host is this machine: `localhost`, or a loopback address.
    pub fn loopback(&self) -> bool {
        let host = self.host.to_ascii_lowercase();
        host == "localhost"
            || host.parse::<IpAddr>().is_ok_and(|ip| match ip {
                IpAddr::V4(v4) => v4.is_loopback(),
                IpAddr::V6(v6) => v6.is_loopback() || v6.to_ipv4_mapped().is_some_and(|v4| v4.is_loopback()),
            })
    }

    /// What the `Host` header says.
    fn host_header(&self) -> String {
        let host = if self.host.contains(':') { format!("[{}]", self.host) } else { self.host.clone() };
        if self.port == if self.tls { 443 } else { 80 } { host } else { format!("{host}:{}", self.port) }
    }
}

/// The Claude API, reached with one key at one endpoint.
pub struct ClaudeApi {
    key: Secret,
    endpoint: Option<Endpoint>,
    outside: Arc<dyn Outside>,
    timeout: Duration,
    retries: u32,
}

impl std::fmt::Debug for ClaudeApi {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ClaudeApi").field("endpoint", &self.endpoint).finish_non_exhaustive()
    }
}

/// How an attempt went: a response, or a connection that didn't go or was lost.
enum Attempt {
    Answered(http::Response),
    Lost(Lost),
}

impl ClaudeApi {
    /// The API at `base_url` (DEFAULT_URL when None or empty), called with `key`. A URL that names no HTTP endpoint
    /// makes every call fail as unreachable.
    pub fn new(key: Secret, base_url: Option<&str>, outside: Arc<dyn Outside>) -> ClaudeApi {
        let url = base_url.filter(|u| !u.trim().is_empty()).unwrap_or(DEFAULT_URL);
        ClaudeApi { key, endpoint: Endpoint::parse(url), outside, timeout: TIMEOUT, retries: RETRIES }
    }

    /// The same, waiting `timeout` for each answer and sending a call again up to `retries` times.
    pub fn with_limits(mut self, timeout: Duration, retries: u32) -> ClaudeApi {
        self.timeout = timeout;
        self.retries = retries;
        self
    }

    fn connect(&self, endpoint: &Endpoint) -> Result<Stream, Lost> {
        self.outside.reach(&endpoint.host).map_err(|_| Lost("Refused"))?;
        let tcp = mm_net::connect(&endpoint.host, endpoint.port, self.timeout)?;
        if !endpoint.tls {
            return Ok(Stream::Plain(tcp));
        }
        // Verified on the system's roots, on this machine as off it: no pin stands in for the chain here.
        let config = mm_net::tls::config(false)?;
        mm_net::handshake(config, mm_net::server_name(&endpoint.host)?, tcp)
    }

    fn attempt(&self, endpoint: &Endpoint, request: &[u8]) -> Attempt {
        let mut stream = match self.connect(endpoint) {
            Ok(stream) => stream,
            Err(lost) => return Attempt::Lost(lost),
        };
        let answered = http::exchange(&mut stream, request);
        stream.close_notify();
        match answered {
            Ok(response) => Attempt::Answered(response),
            Err(lost) => Attempt::Lost(lost),
        }
    }
}

/// How long to wait before sending again, as the SDK waits: what `retry-after-ms` or `retry-after` says when it is
/// up to a minute, else half a second, doubling each time, up to eight.
fn backoff(response: Option<&http::Response>, retried: u32) -> Duration {
    let said = response.and_then(|r| {
        let ms = r.header("retry-after-ms").and_then(|v| v.trim().parse::<f64>().ok()).map(|ms| ms / 1000.0);
        ms.or_else(|| r.header("retry-after").and_then(|v| v.trim().parse::<f64>().ok()))
    });
    match said {
        Some(seconds) if (0.0..=60.0).contains(&seconds) => Duration::from_secs_f64(seconds),
        _ => Duration::from_secs_f64((0.5 * f64::from(1u32 << retried.min(4))).min(8.0)),
    }
}

impl LanguageModel for ClaudeApi {
    fn complete(&self, prompt: &Prompt) -> Result<Reply, ModelError> {
        let Some(endpoint) = &self.endpoint else {
            return Err(ModelError::new("Claude API unreachable: no API address", None));
        };
        // The key goes in the clear only to this machine.
        if !endpoint.tls && !endpoint.loopback() {
            return Err(ModelError::new("Claude API unreachable: plain HTTP is only for this machine", None));
        }
        let body = serde_json::to_vec(&wire::body(prompt)).map_err(|_| ModelError::new("Claude API failed", None))?;
        let mut headers = vec![
            ("Accept", "application/json"),
            ("Content-Type", "application/json"),
            ("Anthropic-Version", VERSION),
            ("X-Api-Key", self.key.expose()),
        ];
        if prompt.fallback {
            headers.push(("Anthropic-Beta", FALLBACK_BETA));
        }
        if self.key.expose().contains(['\r', '\n']) {
            return Err(ModelError::new("Claude API failed: the key can't be sent", None));
        }
        let request =
            http::request(&format!("{}/v1/messages", endpoint.base), &endpoint.host_header(), &headers, &body);
        let mut retried = 0;
        loop {
            let attempt = self.attempt(endpoint, &request);
            let again = match &attempt {
                Attempt::Answered(response) => RETRIED.contains(&response.status),
                Attempt::Lost(lost) => lost.0 != "Refused",
            };
            if again && retried < self.retries {
                let response = match &attempt {
                    Attempt::Answered(response) => Some(response),
                    Attempt::Lost(_) => None,
                };
                std::thread::sleep(backoff(response, retried));
                retried += 1;
                continue;
            }
            return match attempt {
                Attempt::Lost(Lost("Timeout")) => {
                    Err(ModelError::new("Claude API unreachable: Request timed out.", None))
                }
                Attempt::Lost(_) => Err(ModelError::new("Claude API unreachable: Connection error.", None)),
                Attempt::Answered(response) if (200..300).contains(&response.status) => {
                    match serde_json::from_slice::<serde_json::Value>(&response.body) {
                        Ok(parsed) => Ok(wire::reply(&parsed, &prompt.model)),
                        Err(_) => Err(ModelError::new("Claude API failed: APIResponseValidationError", None)),
                    }
                }
                Attempt::Answered(response) => Err(ModelError::new(
                    format!("Claude API {}: {}", response.status, wire::error_text(&response.body)),
                    Some(response.status),
                )),
            };
        }
    }
}
