//! A domain's mail servers, as its MX records name them, asked of a nameserver over UDP
//! (`mailmind.accounts.adapters.mail_hosts`): one question, as RFC 1035 writes it, and the answer read with its names'
//! compression followed and no further than it goes. The system resolves a name for its addresses alone, so the one
//! record type is asked for here, of the nameservers `/etc/resolv.conf` lists (`nameservers`), which the caller reads
//! and asks `Outside` about. Nothing is cached.
//!
//! An answer counts only from the nameserver asked (the socket is connected to it), to the question asked (its id,
//! the caller's at random, and its question match). A nameserver that is silent for the time given, or answers that
//! it failed, is passed over for the next.

use std::net::{IpAddr, SocketAddr, UdpSocket};
use std::time::{Duration, Instant};

/// The port a nameserver listens on.
pub const PORT: u16 = 53;
/// The record type asked for: a domain's mail exchangers.
const MX: u16 = 15;
/// The class: the Internet's.
const IN: u16 = 1;
/// The bytes read of a datagram, more than one carries without EDNS.
const LONGEST_ANSWER: usize = 4096;
/// The longest a name may be, in text.
const LONGEST_NAME: usize = 253;
/// The compression pointers followed in one name: a loop of them ends there.
const MOST_JUMPS: usize = 64;

/// Each `nameserver` line's address in a resolv.conf, in its order, on port 53. One that isn't an address, or names
/// the network interface it is reached through (`fe80::1%en0`), is left out.
pub fn nameservers(resolv_conf: &str) -> Vec<SocketAddr> {
    resolv_conf
        .lines()
        .filter_map(|line| {
            let mut words = line.split_whitespace();
            match (words.next(), words.next()) {
                (Some("nameserver"), Some(address)) if !address.contains('%') => address.parse::<IpAddr>().ok(),
                _ => None,
            }
        })
        .map(|address| SocketAddr::new(address, PORT))
        .collect()
}

/// The question of `domain`'s MX records, with the id `ident`, recursion desired; None for a domain whose labels no
/// question can carry.
pub fn query(ident: u16, domain: &str) -> Option<Vec<u8>> {
    let labels: Vec<&str> = domain.split('.').collect();
    if domain.len() > LONGEST_NAME || !labels.iter().all(|l| !l.is_empty() && l.len() <= 63 && l.is_ascii()) {
        return None;
    }
    let mut out = Vec::with_capacity(18 + domain.len());
    for field in [ident, 0x0100, 1, 0, 0, 0] {
        out.extend_from_slice(&field.to_be_bytes());
    }
    for label in labels {
        out.push(u8::try_from(label.len()).ok()?);
        out.extend_from_slice(label.as_bytes());
    }
    out.push(0);
    out.extend_from_slice(&MX.to_be_bytes());
    out.extend_from_slice(&IN.to_be_bytes());
    Some(out)
}

/// What a datagram is, read as the answer to `query(ident, domain)`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Answer {
    /// Not that answer: another question's, a question itself, or bytes that don't read.
    Foreign,
    /// That answer, saying the nameserver couldn't: the next is asked.
    Failed,
    /// The hosts its MX records name, in its order: none when the domain has none or doesn't exist.
    Hosts(Vec<String>),
}

fn u16_at(data: &[u8], at: usize) -> Option<u16> {
    Some(u16::from_be_bytes([*data.get(at)?, *data.get(at + 1)?]))
}

/// What `data` says, as the answer to `query(ident, domain)`.
pub fn answer(ident: u16, domain: &str, data: &[u8]) -> Answer {
    let head = (u16_at(data, 0), u16_at(data, 2), u16_at(data, 4), u16_at(data, 6));
    let (Some(got), Some(flags), Some(questions), Some(answers)) = head else { return Answer::Foreign };
    if data.len() < 12 || got != ident || flags & 0x8000 == 0 || questions != 1 {
        return Answer::Foreign;
    }
    let Some((asked, at)) = name(data, 12) else { return Answer::Foreign };
    if asked != domain.to_ascii_lowercase() || u16_at(data, at) != Some(MX) || u16_at(data, at + 2) != Some(IN) {
        return Answer::Foreign;
    }
    match flags & 0x000F {
        3 => return Answer::Hosts(Vec::new()),
        0 => {}
        _ => return Answer::Failed,
    }
    let mut at = at + 4;
    let mut hosts = Vec::new();
    for _ in 0..answers {
        // A record cut short, as a truncated answer's last is, ends what is read.
        let Some((_, after)) = name(data, at) else { break };
        let fields = (u16_at(data, after), u16_at(data, after + 2), u16_at(data, after + 8));
        let (Some(kind), Some(class), Some(length)) = fields else { break };
        at = after + 10;
        let end = at + usize::from(length);
        if end > data.len() {
            break;
        }
        if kind == MX && class == IN && length > 2 {
            let Some((host, _)) = name(data, at + 2) else { break };
            hosts.push(host);
        }
        at = end;
    }
    Answer::Hosts(hosts)
}

/// The name at `at`, in lower case and without a final dot, and where what follows it starts. None for one that runs
/// past the data, loops, is longer than a name can be or isn't ASCII.
fn name(data: &[u8], mut at: usize) -> Option<(String, usize)> {
    let mut labels: Vec<String> = Vec::new();
    let mut after = None;
    let mut jumps = 0;
    loop {
        let n = *data.get(at)?;
        if n & 0xC0 == 0xC0 {
            // A pointer to the rest of the name, earlier in the data.
            let low = *data.get(at + 1)?;
            if jumps >= MOST_JUMPS {
                return None;
            }
            after = after.or(Some(at + 2));
            jumps += 1;
            at = (usize::from(n & 0x3F) << 8) | usize::from(low);
            continue;
        }
        if n & 0xC0 != 0 {
            return None;
        }
        at += 1;
        if n == 0 {
            break;
        }
        let label = data.get(at..at + usize::from(n))?;
        if !label.is_ascii() {
            return None;
        }
        labels.push(String::from_utf8_lossy(label).to_ascii_lowercase());
        at += usize::from(n);
    }
    let name = labels.join(".");
    if name.len() > LONGEST_NAME {
        return None;
    }
    Some((name, after.unwrap_or(at)))
}

/// The hosts `domain`'s MX records name, as the first of `nameservers` to answer says: none when it has none, or when
/// none answered within `timeout` or each said it failed. `ident` is the question's id, which the caller draws at
/// random.
pub fn mail_hosts(nameservers: &[SocketAddr], domain: &str, ident: u16, timeout: Duration) -> Vec<String> {
    let Some(question) = query(ident, domain) else { return Vec::new() };
    nameservers.iter().find_map(|nameserver| ask(*nameserver, &question, ident, domain, timeout)).unwrap_or_default()
}

/// What `nameserver` answers, or None when it didn't, or said it couldn't.
fn ask(nameserver: SocketAddr, question: &[u8], ident: u16, domain: &str, timeout: Duration) -> Option<Vec<String>> {
    let local: SocketAddr = match nameserver {
        SocketAddr::V4(_) => SocketAddr::from(([0, 0, 0, 0], 0)),
        SocketAddr::V6(_) => SocketAddr::from(([0u16; 8], 0)),
    };
    let socket = UdpSocket::bind(local).ok()?;
    socket.connect(nameserver).ok()?;
    socket.set_read_timeout(Some(timeout)).ok()?;
    socket.send(question).ok()?;
    let deadline = Instant::now() + timeout;
    let mut buffer = vec![0u8; LONGEST_ANSWER];
    loop {
        let left = deadline.checked_duration_since(Instant::now()).filter(|left| !left.is_zero())?;
        socket.set_read_timeout(Some(left)).ok()?;
        let read = socket.recv(&mut buffer).ok()?;
        match answer(ident, domain, buffer.get(..read)?) {
            Answer::Foreign => continue,
            Answer::Failed => return None,
            Answer::Hosts(hosts) => return Some(hosts),
        }
    }
}
