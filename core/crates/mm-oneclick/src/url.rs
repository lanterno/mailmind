//! A URL as the Python app's `urllib.parse` (3.14) reads one for the one-click POST: `urlsplit`'s parts, the host and
//! port its netloc names, and `urljoin` for a redirect's `Location`.
//!
//! Python reads a URL leniently: leading control characters and spaces go, tabs and line breaks anywhere go, and the
//! scheme is any run of letters, digits, `+`, `-` and `.` before the first colon. What Python refuses with a ValueError
//! (unbalanced or unreadable brackets, a port that isn't a number in range) is `Unreadable` here.

use std::net::Ipv6Addr;

/// A URL Python's `urllib.parse` raises a ValueError on.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Unreadable;

/// `_urlsplit`'s parts, each None where the URL has none (`netloc` without `//`, `query` without `?`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Parts {
    pub scheme: Option<String>,
    pub netloc: Option<String>,
    pub path: String,
    pub query: Option<String>,
    pub fragment: Option<String>,
}

/// What `_WHATWG_C0_CONTROL_OR_SPACE` holds.
fn c0_or_space(c: char) -> bool {
    c <= ' '
}

/// `urlsplit(url)`.
pub fn split(url: &str) -> Result<Parts, Unreadable> {
    let cleaned: String =
        url.trim_start_matches(c0_or_space).chars().filter(|c| !matches!(c, '\t' | '\r' | '\n')).collect();
    let mut rest = cleaned.as_str();
    let mut scheme = None;
    if let Some(i) = rest.find(':') {
        let named = &rest[..i];
        let first = named.chars().next();
        if first.is_some_and(|c| c.is_ascii_alphabetic())
            && named.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '+' | '-' | '.'))
        {
            scheme = Some(named.to_ascii_lowercase());
            rest = &rest[i + 1..];
        }
    }
    let mut netloc = None;
    if let Some(after) = rest.strip_prefix("//") {
        let end = after.find(['/', '?', '#']).unwrap_or(after.len());
        let named = &after[..end];
        if named.contains('[') != named.contains(']') {
            return Err(Unreadable);
        }
        if named.contains('[') {
            check_bracketed_netloc(named)?;
        }
        netloc = Some(named.to_owned());
        rest = &after[end..];
    }
    let mut fragment = None;
    if let Some((before, after)) = rest.split_once('#') {
        fragment = Some(after.to_owned());
        rest = before;
    }
    let mut query = None;
    if let Some((before, after)) = rest.split_once('?') {
        query = Some(after.to_owned());
        rest = before;
    }
    Ok(Parts { scheme, netloc, path: rest.to_owned(), query, fragment })
}

/// `_check_bracketed_netloc`: nothing before a bracket, nothing between it and the port, and an IPv6 address or an
/// IPvFuture inside.
fn check_bracketed_netloc(netloc: &str) -> Result<(), Unreadable> {
    let host_and_port = netloc.rsplit_once('@').map_or(netloc, |(_, after)| after);
    let host = match host_and_port.split_once('[') {
        Some((before, bracketed)) => {
            if !before.is_empty() {
                return Err(Unreadable);
            }
            let (host, port) = bracketed.split_once(']').unwrap_or((bracketed, ""));
            if !port.is_empty() && !port.starts_with(':') {
                return Err(Unreadable);
            }
            host
        }
        None => host_and_port.split_once(':').map_or(host_and_port, |(host, _)| host),
    };
    check_bracketed_host(host)
}

/// `_check_bracketed_host`: `v`, hex digits, a dot and something after it; else an IPv6 address, a zone allowed.
fn check_bracketed_host(host: &str) -> Result<(), Unreadable> {
    if let Some(future) = host.strip_prefix('v') {
        let (version, after) = future.split_once('.').ok_or(Unreadable)?;
        let good = !version.is_empty() && version.chars().all(|c| c.is_ascii_hexdigit()) && !after.is_empty();
        return if good { Ok(()) } else { Err(Unreadable) };
    }
    let address = match host.split_once('%') {
        Some((_, zone)) if zone.is_empty() || zone.contains('%') => return Err(Unreadable),
        Some((address, _)) => address,
        None => host,
    };
    address.parse::<Ipv6Addr>().map(|_| ()).map_err(|_| Unreadable)
}

impl Parts {
    /// `_hostinfo`: the host and port of the netloc, past any login.
    fn host_info(&self) -> (&str, Option<&str>) {
        let netloc = self.netloc.as_deref().unwrap_or_default();
        let info = netloc.rsplit_once('@').map_or(netloc, |(_, after)| after);
        let (host, port) = match info.split_once('[') {
            Some((_, bracketed)) => {
                let (host, after) = bracketed.split_once(']').unwrap_or((bracketed, ""));
                (host, after.split_once(':').map_or("", |(_, port)| port))
            }
            None => info.split_once(':').unwrap_or((info, "")),
        };
        (host, Some(port).filter(|p| !p.is_empty()))
    }

    /// `.hostname`: lower-case but for an IPv6 zone, None where the netloc names none.
    pub fn hostname(&self) -> Option<String> {
        let (host, _) = self.host_info();
        if host.is_empty() {
            return None;
        }
        Some(match host.split_once('%') {
            Some((name, zone)) => format!("{}%{zone}", name.to_lowercase()),
            None => host.to_lowercase(),
        })
    }

    /// `.port`: None where the netloc names none, and Unreadable where what it names isn't a port.
    pub fn port(&self) -> Result<Option<u16>, Unreadable> {
        match self.host_info().1 {
            None => Ok(None),
            Some(port) if port.bytes().all(|b| b.is_ascii_digit()) => {
                let digits = port.trim_start_matches('0');
                if digits.len() > 5 {
                    return Err(Unreadable);
                }
                if digits.is_empty() { Ok(Some(0)) } else { digits.parse().map(Some).map_err(|_| Unreadable) }
            }
            Some(_) => Err(Unreadable),
        }
    }
}

/// `_urlunsplit`.
fn unsplit(
    scheme: Option<&str>,
    netloc: Option<&str>,
    path: &str,
    query: Option<&str>,
    fragment: Option<&str>,
) -> String {
    let mut url = String::new();
    if let Some(scheme) = scheme.filter(|s| !s.is_empty()) {
        url.push_str(scheme);
        url.push(':');
    }
    match netloc {
        Some(netloc) => {
            url.push_str("//");
            url.push_str(netloc);
            if !path.is_empty() && !path.starts_with('/') {
                url.push('/');
            }
        }
        None if path.starts_with("//") => url.push_str("//"),
        None => {}
    }
    url.push_str(path);
    if let Some(query) = query {
        url.push('?');
        url.push_str(query);
    }
    if let Some(fragment) = fragment {
        url.push('#');
        url.push_str(fragment);
    }
    url
}

/// `urljoin(base, reference)`: where a redirect's `Location` points, read against the URL that answered it. The base
/// is an https URL, and https is a scheme `urljoin` resolves against and whose URLs have a netloc; a reference of
/// another scheme is itself.
pub fn join(base: &str, reference: &str) -> Result<String, Unreadable> {
    if base.is_empty() {
        return Ok(reference.to_owned());
    }
    if reference.is_empty() {
        return Ok(base.to_owned());
    }
    let b = split(base)?;
    let r = split(reference)?;
    let scheme = r.scheme.clone().or_else(|| b.scheme.clone());
    if scheme != b.scheme {
        return Ok(reference.to_owned());
    }
    if r.netloc.as_deref().is_some_and(|n| !n.is_empty()) {
        return Ok(unsplit(scheme.as_deref(), r.netloc.as_deref(), &r.path, r.query.as_deref(), r.fragment.as_deref()));
    }
    let netloc = b.netloc.clone();
    if r.path.is_empty() {
        let (query, fragment) = match r.query {
            None => (b.query.clone(), r.fragment.clone().or(b.fragment.clone())),
            Some(query) => (Some(query), r.fragment.clone()),
        };
        return Ok(unsplit(scheme.as_deref(), netloc.as_deref(), &b.path, query.as_deref(), fragment.as_deref()));
    }
    let mut base_parts: Vec<&str> = b.path.split('/').collect();
    if base_parts.last().is_some_and(|last| !last.is_empty()) {
        base_parts.pop();
    }
    let segments: Vec<&str> = if r.path.starts_with('/') {
        r.path.split('/').collect()
    } else {
        let mut joined: Vec<&str> = base_parts;
        joined.extend(r.path.split('/'));
        // What would leave a doubled slash, between the first and the last.
        let last = joined.len().saturating_sub(1);
        joined.iter().enumerate().filter(|(i, s)| *i == 0 || *i == last || !s.is_empty()).map(|(_, s)| *s).collect()
    };
    let mut resolved: Vec<&str> = Vec::new();
    for segment in &segments {
        match *segment {
            ".." => {
                resolved.pop();
            }
            "." => {}
            other => resolved.push(other),
        }
    }
    if segments.last().is_some_and(|last| matches!(*last, "." | "..")) {
        resolved.push("");
    }
    let path = resolved.join("/");
    let path = if path.is_empty() { "/".to_owned() } else { path };
    Ok(unsplit(scheme.as_deref(), netloc.as_deref(), &path, r.query.as_deref(), r.fragment.as_deref()))
}
