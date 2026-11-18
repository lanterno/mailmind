//! An IMAP server on loopback for the tests that keep connections and backfill bodies: TLS from the first byte,
//! folders of fictional mail for each account, and every command written down with the connection it came on. It
//! answers a command that needs a folder selected, before one is, with BAD, as a server does; and it can end every
//! connection at once, as a server's idle timeout or a sleep would, or the next NOOP's.

#![allow(clippy::unwrap_used, dead_code)]

use std::collections::{BTreeMap, BTreeSet};
use std::io::{BufRead, BufReader, Write};
use std::net::{Shutdown, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};

use mm_kernel::seams::{Clock, Keychain, KeychainProblem, Moment, Outside, Secret, Tripped};
use mm_server::environment::{Environment, Settings};
use mm_server::{App, Seams};
use rcgen::{CertificateParams, KeyPair};
use rustls::pki_types::{PrivateKeyDer, PrivatePkcs8KeyDer};
use rustls::{ServerConfig, ServerConnection, StreamOwned};
use serde_json::{Value, json};

pub const ADA: &str = "ada@example.test";
pub const BEA: &str = "bea@example.test";
pub const PASSWORD: &str = "app-password";
pub const KEY: [u8; 32] = [0x5e; 32];
/// 2026-11-02 12:00:00 UTC.
pub const NOW: i64 = 1_793_620_800;

pub fn until(mut what: impl FnMut() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(20);
    while !what() {
        assert!(Instant::now() < deadline, "it never came");
        std::thread::sleep(Duration::from_millis(5));
    }
}

/// A short note dated `date` (as a Date header has it), saying `words`.
pub fn note_on(uid: u32, date: &str, words: &str) -> Vec<u8> {
    format!(
        "From: Lin <lin@example.test>\r\nTo: Ada <{ADA}>\r\nSubject: Note {uid}\r\nDate: {date}\r\n\
         Message-ID: <note-{uid}@example.test>\r\n\r\n{words}\r\n"
    )
    .into_bytes()
}

/// A short note of 2 November 2026.
pub fn note(uid: u32) -> Vec<u8> {
    note_on(uid, "Mon, 02 Nov 2026 09:00:00 +0000", &format!("Note {uid} says hello."))
}

/// A message of a line of text and a PDF of about `kib` KiB.
pub fn with_pdf(uid: u32, words: &str, kib: usize) -> Vec<u8> {
    let mut raw = format!(
        "From: Lin <lin@example.test>\r\nTo: Ada <{ADA}>\r\nSubject: Plans {uid}\r\n\
         Date: Mon, 02 Nov 2026 09:00:00 +0000\r\nMessage-ID: <plans-{uid}@example.test>\r\nMIME-Version: 1.0\r\n\
         Content-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\n{words}\r\n\
         --b\r\nContent-Type: application/pdf; name=plans.pdf\r\nContent-Transfer-Encoding: base64\r\n\r\n"
    );
    // 66 bytes a line.
    raw.push_str(&"JVBERi0xLjQKJcfsj6IKNSAwIG9iago8PC9MZW5ndGggNiAwIFI+PgpzdHJlYW0K\r\n".repeat(kib * 1024 / 66));
    raw.push_str("--b--\r\n");
    raw.into_bytes()
}

/// A command as the server heard it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Heard {
    pub user: String,
    /// Which connection, counted from 1 as they came.
    pub connection: usize,
    /// Without its tag.
    pub command: String,
}

#[derive(Debug, Default)]
pub struct State {
    pub passwords: BTreeMap<String, String>,
    /// Each (account, folder)'s messages by UID.
    pub folders: BTreeMap<(String, String), BTreeMap<u32, Vec<u8>>>,
    pub heard: Vec<Heard>,
    connections: usize,
    /// Each connection's socket, while it is open.
    open: BTreeMap<usize, TcpStream>,
    /// The connections that have ended, however they ended.
    pub ended: BTreeSet<usize>,
    /// The next NOOPs end their connection unanswered, this many.
    pub noops_dropped: usize,
    /// The next UID FETCHes end their connection unanswered, this many.
    pub fetches_dropped: usize,
    /// FETCH says no RFC822.SIZE.
    pub no_sizes: bool,
    /// The next NOOPs are answered three seconds late, this many.
    pub slow_noops: usize,
    /// Each (account, folder, UID)'s flags, where it has any.
    pub flags: BTreeMap<(String, String, u32), BTreeSet<String>>,
    /// Each (account, folder)'s next UID, past every one it has held.
    uid_next: BTreeMap<(String, String), u32>,
    /// The server is away: each connection hears BYE as it opens, and nothing more.
    pub away: bool,
    /// A SELECT of a folder it doesn't hold says so in RFC 5530's code, `NO [NONEXISTENT]`, as Dovecot's and Gmail's
    /// do; else a bare NO.
    pub says_nonexistent: bool,
    /// The next SELECTs and EXAMINEs of a folder it holds are refused with a bare NO, this many: a folder it can't
    /// open for now.
    pub selects_refused: usize,
    /// LIST is refused.
    pub lists_refused: bool,
}

impl State {
    /// The UID a message added to `account`'s `folder` takes: one no message there has had.
    fn next_uid(&mut self, account: &str, folder: &str) -> u32 {
        let key = (account.to_owned(), folder.to_owned());
        let held = self.folders.get(&key).and_then(|m| m.keys().max().copied()).unwrap_or(0);
        let next = self.uid_next.get(&key).copied().unwrap_or(1).max(held + 1);
        self.uid_next.insert(key, next + 1);
        next
    }
}

/// The Message-ID a message's headers give, without its angle brackets' spaces.
fn message_id(raw: &[u8]) -> Option<String> {
    let text = String::from_utf8_lossy(raw);
    let head = text.split("\r\n\r\n").next().unwrap_or_default();
    head.lines()
        .find_map(|line| line.split_once(':').filter(|(name, _)| name.eq_ignore_ascii_case("message-id")))
        .map(|(_, value)| value.trim().to_owned())
}

#[derive(Clone, Debug)]
pub struct Imap {
    pub port: u16,
    pub state: Arc<Mutex<State>>,
}

impl Imap {
    pub fn start() -> Imap {
        let key = KeyPair::generate().unwrap();
        let cert = CertificateParams::new(vec!["127.0.0.1".to_owned()]).unwrap().self_signed(&key).unwrap();
        let config = ServerConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
            .with_safe_default_protocol_versions()
            .unwrap()
            .with_no_client_auth()
            .with_single_cert(
                vec![cert.der().clone()],
                PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(key.serialize_der())),
            )
            .unwrap();
        let config = Arc::new(config);
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let imap = Imap { port: listener.local_addr().unwrap().port(), state: Arc::default() };
        let state = imap.state.clone();
        std::thread::spawn(move || {
            for tcp in listener.incoming() {
                let Ok(tcp) = tcp else { return };
                let id = {
                    let mut state = state.lock().unwrap();
                    state.connections += 1;
                    let id = state.connections;
                    state.open.insert(id, tcp.try_clone().unwrap());
                    id
                };
                let tls = StreamOwned::new(ServerConnection::new(config.clone()).unwrap(), tcp);
                let state = state.clone();
                std::thread::spawn(move || {
                    let mut connection = Connection {
                        io: BufReader::new(tls),
                        state: state.clone(),
                        user: String::new(),
                        id,
                        folder: None,
                    };
                    let _ = connection.serve();
                    let mut state = state.lock().unwrap();
                    state.open.remove(&id);
                    state.ended.insert(id);
                });
            }
        });
        imap
    }

    pub fn held(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap()
    }

    pub fn add(&self, email: &str, folder: &str, messages: impl IntoIterator<Item = (u32, Vec<u8>)>) {
        let mut state = self.held();
        state.passwords.insert(email.to_owned(), PASSWORD.to_owned());
        state.folders.entry((email.to_owned(), folder.to_owned())).or_default().extend(messages);
    }

    /// What `user` was sent that starts with `start`, in order.
    pub fn sent(&self, user: &str, start: &str) -> Vec<String> {
        self.held()
            .heard
            .iter()
            .filter(|h| h.user == user && h.command.starts_with(start))
            .map(|h| h.command.clone())
            .collect()
    }

    /// The same, with the connection each came on.
    pub fn sent_on(&self, user: &str, start: &str) -> Vec<(usize, String)> {
        let state = self.held();
        state
            .heard
            .iter()
            .filter(|h| h.user == user && h.command.starts_with(start))
            .map(|h| (h.connection, h.command.clone()))
            .collect()
    }

    /// How many commands have been heard: a mark to read from.
    pub fn mark(&self) -> usize {
        self.held().heard.len()
    }

    /// What `user` was sent since `mark`, without tags.
    pub fn since(&self, mark: usize, user: &str) -> Vec<String> {
        self.held().heard[mark..].iter().filter(|h| h.user == user).map(|h| h.command.clone()).collect()
    }

    /// The connections open now.
    pub fn open(&self) -> usize {
        self.held().open.len()
    }

    /// The server goes away, or comes back: away, each connection hears BYE as it opens, and those open now end.
    pub fn away(&self, away: bool) {
        self.held().away = away;
        if away {
            self.end_every_connection();
        }
    }

    /// The Message-IDs `email`'s `folder` holds, in UID order.
    pub fn ids_in(&self, email: &str, folder: &str) -> Vec<String> {
        let state = self.held();
        let held = state.folders.get(&(email.to_owned(), folder.to_owned()));
        held.map(|m| m.values().filter_map(|raw| message_id(raw)).collect()).unwrap_or_default()
    }

    /// The flags of the message with Message-ID `id` in `email`'s `folder`.
    pub fn flags_of(&self, email: &str, folder: &str, id: &str) -> BTreeSet<String> {
        let state = self.held();
        let key = (email.to_owned(), folder.to_owned());
        let uid =
            state.folders.get(&key).and_then(|m| m.iter().find(|(_, raw)| message_id(raw).as_deref() == Some(id)));
        uid.and_then(|(uid, _)| state.flags.get(&(key.0.clone(), key.1.clone(), *uid)).cloned()).unwrap_or_default()
    }

    /// Another client removes message `uid` from `email`'s `folder` for good.
    pub fn expunge(&self, email: &str, folder: &str, uid: u32) {
        let mut state = self.held();
        if let Some(held) = state.folders.get_mut(&(email.to_owned(), folder.to_owned())) {
            held.remove(&uid);
        }
        state.flags.remove(&(email.to_owned(), folder.to_owned(), uid));
    }

    /// Another client deletes `email`'s `folder` and all it held.
    pub fn delete_folder(&self, email: &str, folder: &str) {
        let mut state = self.held();
        state.folders.remove(&(email.to_owned(), folder.to_owned()));
        state.flags.retain(|(account, name, _), _| !(account == email && name == folder));
    }

    /// Every connection ends now, unannounced: a server's idle timeout, or a network gone.
    pub fn end_every_connection(&self) {
        let open: Vec<TcpStream> = std::mem::take(&mut self.held().open).into_values().collect();
        for tcp in open {
            let _ = tcp.shutdown(Shutdown::Both);
        }
    }
}

/// The UIDs a set names (`1,2,5`, `3:9`, `701:*`), of those held.
fn named(set: &str, held: &BTreeMap<u32, Vec<u8>>) -> Vec<u32> {
    let top = held.keys().max().copied().unwrap_or(0);
    let mut out = BTreeSet::new();
    for part in set.split(',') {
        let (from, to) = match part.split_once(':') {
            Some((from, "*")) => (from.parse::<u32>().unwrap().min(top), top),
            Some((from, to)) => (from.parse().unwrap(), to.parse().unwrap()),
            None => (part.parse().unwrap(), part.parse().unwrap()),
        };
        out.extend(held.keys().copied().filter(|uid| (from..=to).contains(uid)));
    }
    out.into_iter().collect()
}

struct Connection {
    io: BufReader<StreamOwned<ServerConnection, TcpStream>>,
    state: Arc<Mutex<State>>,
    user: String,
    id: usize,
    folder: Option<String>,
}

impl Connection {
    fn say(&mut self, said: &[u8]) -> std::io::Result<()> {
        let stream = self.io.get_mut();
        stream.write_all(said)?;
        stream.flush()
    }

    fn held(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap()
    }

    fn serve(&mut self) -> std::io::Result<()> {
        if self.held().away {
            return self.say(b"* BYE the server is away\r\n");
        }
        self.say(b"* OK [CAPABILITY IMAP4rev1] ready\r\n")?;
        let mut line = String::new();
        loop {
            line.clear();
            if self.io.read_line(&mut line)? == 0 {
                return Ok(());
            }
            let line = line.trim_end_matches(['\r', '\n']).to_owned();
            let (tag, command) = line.split_once(' ').unwrap_or((&line, ""));
            if !self.answer(tag, command)? {
                return Ok(());
            }
        }
    }

    /// Answer one command: false once the connection is to end.
    fn answer(&mut self, tag: &str, command: &str) -> std::io::Result<bool> {
        let verb = command.split(' ').next().unwrap_or("").to_uppercase();
        if verb == "LOGIN" {
            let quoted: Vec<&str> = command.split('"').collect();
            let (user, password) = (quoted.get(1).copied().unwrap_or(""), quoted.get(3).copied().unwrap_or(""));
            self.held().heard.push(Heard { user: user.to_owned(), connection: self.id, command: "LOGIN".to_owned() });
            if self.held().passwords.get(user).is_some_and(|p| p == password) {
                self.user = user.to_owned();
                self.say(format!("{tag} OK [CAPABILITY IMAP4rev1] logged in\r\n").as_bytes())?;
            } else {
                self.say(format!("{tag} NO [AUTHENTICATIONFAILED] invalid credentials\r\n").as_bytes())?;
            }
            return Ok(true);
        }
        let user = self.user.clone();
        self.held().heard.push(Heard { user: user.clone(), connection: self.id, command: command.to_owned() });
        let upper = command.to_uppercase();
        let selected = self.folder.clone();
        let messages = |state: &State| -> BTreeMap<u32, Vec<u8>> {
            selected.as_ref().and_then(|f| state.folders.get(&(user.clone(), f.clone())).cloned()).unwrap_or_default()
        };
        let needs_folder = upper.starts_with("UID ") || upper.starts_with("FETCH") || upper.starts_with("STORE");
        if needs_folder && self.folder.is_none() {
            self.say(format!("{tag} BAD no mailbox selected\r\n").as_bytes())?;
            return Ok(true);
        }
        match verb.as_str() {
            "CAPABILITY" => self.say(format!("* CAPABILITY IMAP4rev1\r\n{tag} OK\r\n").as_bytes())?,
            "NOOP" => {
                let (drop, slow) = {
                    let mut state = self.held();
                    let drop = state.noops_dropped > 0;
                    state.noops_dropped = state.noops_dropped.saturating_sub(1);
                    let slow = !drop && state.slow_noops > 0;
                    state.slow_noops = state.slow_noops.saturating_sub(usize::from(slow));
                    (drop, slow)
                };
                if drop {
                    return Ok(false);
                }
                if slow {
                    std::thread::sleep(Duration::from_secs(3));
                }
                self.say(format!("{tag} OK\r\n").as_bytes())?;
            }
            "SELECT" | "EXAMINE" => {
                let name = command.split_once(' ').map(|(_, n)| n.trim_matches('"').to_owned()).unwrap_or_default();
                let held = self.held().folders.get(&(user.clone(), name.clone())).map(BTreeMap::len);
                let refused = held.is_some() && {
                    let mut state = self.held();
                    let refused = state.selects_refused > 0;
                    state.selects_refused = state.selects_refused.saturating_sub(1);
                    refused
                };
                match held {
                    Some(_) if refused => {
                        self.folder = None;
                        self.say(format!("{tag} NO can't open it now\r\n").as_bytes())?;
                    }
                    Some(exists) => {
                        self.folder = Some(name);
                        self.say(
                            format!("* {exists} EXISTS\r\n* OK [UIDVALIDITY 7] ok\r\n{tag} OK done\r\n").as_bytes(),
                        )?;
                    }
                    None => {
                        // A SELECT that fails leaves no folder selected.
                        self.folder = None;
                        let code = if self.held().says_nonexistent { "[NONEXISTENT] " } else { "" };
                        self.say(format!("{tag} NO {code}no such mailbox\r\n").as_bytes())?;
                    }
                }
            }
            "LOGOUT" => {
                self.say(format!("* BYE\r\n{tag} OK\r\n").as_bytes())?;
                return Ok(false);
            }
            "LIST" if self.held().lists_refused => {
                self.say(format!("{tag} NO can't list now\r\n").as_bytes())?;
            }
            "LIST" => {
                let mut said = String::new();

                for (_, name) in self.held().folders.keys().filter(|(u, _)| *u == user) {
                    said.push_str(&format!("* LIST (\\HasNoChildren) \"/\" \"{name}\"\r\n"));
                }
                said.push_str(&format!("{tag} OK\r\n"));
                self.say(said.as_bytes())?;
            }
            // A folder made, renamed or deleted: each name quoted, as MailMind sends it.
            "CREATE" | "RENAME" | "DELETE" => {
                let names: Vec<String> = command.split('"').skip(1).step_by(2).map(str::to_owned).collect();
                {
                    let mut state = self.held();
                    let folders = &mut state.folders;
                    match (verb.as_str(), names.as_slice()) {
                        ("CREATE", [name]) => {
                            folders.entry((user.clone(), name.clone())).or_default();
                        }
                        ("RENAME", [from, to]) => {
                            if let Some(held) = folders.remove(&(user.clone(), from.clone())) {
                                folders.insert((user.clone(), to.clone()), held);
                            }
                        }
                        ("DELETE", [name]) => {
                            folders.remove(&(user.clone(), name.clone()));
                        }
                        _ => {}
                    }
                }
                self.say(format!("{tag} OK\r\n").as_bytes())?;
            }
            "UID" if upper.starts_with("UID SEARCH") => {
                let (held, deleted) = {
                    let state = self.held();
                    let held = messages(&state);
                    let folder = selected.clone().unwrap_or_default();
                    let deleted: BTreeSet<u32> = held
                        .keys()
                        .copied()
                        .filter(|uid| {
                            state
                                .flags
                                .get(&(user.clone(), folder.clone(), *uid))
                                .is_some_and(|f| f.contains("\\Deleted"))
                        })
                        .collect();
                    (held, deleted)
                };
                let criteria = &command["UID SEARCH ".len()..];
                let (undeleted, criteria) = match criteria.strip_prefix("UNDELETED") {
                    Some(rest) => (true, rest.trim_start()),
                    None => (false, criteria),
                };
                let mut found: Vec<u32> = match criteria.strip_prefix("UID ") {
                    Some(set) => named(set, &held),
                    None => match criteria.split_once("HEADER Message-ID ") {
                        Some((_, id)) => {
                            let id = id.trim_matches('"');
                            held.iter()
                                .filter(|(_, raw)| message_id(raw).as_deref() == Some(id))
                                .map(|(u, _)| *u)
                                .collect()
                        }
                        None => held.keys().copied().collect(),
                    },
                };
                if undeleted {
                    found.retain(|uid| !deleted.contains(uid));
                }
                let found: Vec<String> = found.iter().map(u32::to_string).collect();
                self.say(format!("* SEARCH {}\r\n{tag} OK\r\n", found.join(" ")).as_bytes())?;
            }
            "UID" if upper.starts_with("UID FETCH") => {
                let (drop, no_sizes, held, flags) = {
                    let mut state = self.held();
                    let drop = state.fetches_dropped > 0;
                    state.fetches_dropped = state.fetches_dropped.saturating_sub(1);
                    let folder = selected.clone().unwrap_or_default();
                    let flags: BTreeMap<u32, String> = state
                        .flags
                        .iter()
                        .filter(|((account, name, _), _)| *account == user && *name == folder)
                        .map(|((_, _, uid), flags)| (*uid, flags.iter().cloned().collect::<Vec<_>>().join(" ")))
                        .collect();
                    (drop, state.no_sizes, messages(&state), flags)
                };
                if drop {
                    return Ok(false);
                }
                let (set, items) = command["UID FETCH ".len()..].split_once(' ').unwrap();
                for uid in named(set, &held) {
                    let raw = &held[&uid];
                    let seq = held.keys().position(|u| *u == uid).unwrap() + 1;
                    let flags = flags.get(&uid).map_or("", String::as_str);
                    let mut head = format!("* {seq} FETCH (UID {uid} FLAGS ({flags})");
                    if items.contains("RFC822.SIZE") && !no_sizes {
                        head.push_str(&format!(" RFC822.SIZE {}", raw.len()));
                    }
                    let data = if let Some(at) = items.find("BODY.PEEK[HEADER.FIELDS") {
                        let section = &items[at + "BODY.PEEK[".len()..at + items[at..].find(']').unwrap()];
                        let end = raw.windows(4).position(|w| w == b"\r\n\r\n").unwrap() + 4;
                        Some((format!("BODY[{section}]"), raw[..end].to_vec()))
                    } else if let Some(at) = items.find("BODY.PEEK[]<0.") {
                        let rest = &items[at + "BODY.PEEK[]<0.".len()..];
                        let bytes: usize = rest[..rest.find('>').unwrap()].parse().unwrap();
                        Some(("BODY[]<0>".to_owned(), raw[..raw.len().min(bytes)].to_vec()))
                    } else if items.contains("BODY.PEEK[]") {
                        Some(("BODY[]".to_owned(), raw.clone()))
                    } else {
                        None
                    };
                    let mut said = head.into_bytes();
                    if let Some((name, data)) = data {
                        said.extend(format!(" {name} {{{}}}\r\n", data.len()).into_bytes());
                        said.extend(data);
                    }
                    said.extend(b")\r\n");
                    self.say(&said)?;
                }
                self.say(format!("{tag} OK done\r\n").as_bytes())?;
            }
            // A copy or a move: each message takes the next UID there, its flags with it.
            "UID" if upper.starts_with("UID COPY ") || upper.starts_with("UID MOVE ") => {
                let moving = upper.starts_with("UID MOVE ");
                let set = command["UID COPY ".len()..].split(' ').next().unwrap_or_default().to_owned();
                let dest = command.split('"').nth(1).unwrap_or_default().to_owned();
                let folder = selected.clone().unwrap_or_default();
                let said = {
                    let mut state = self.held();
                    if !state.folders.contains_key(&(user.clone(), dest.clone())) {
                        format!("{tag} NO [TRYCREATE] no such mailbox\r\n")
                    } else {
                        let held = messages(&state);
                        let (mut from, mut to) = (Vec::new(), Vec::new());
                        for uid in named(&set, &held) {
                            let next = state.next_uid(&user, &dest);
                            let flags = state.flags.get(&(user.clone(), folder.clone(), uid)).cloned();
                            state
                                .folders
                                .get_mut(&(user.clone(), dest.clone()))
                                .unwrap()
                                .insert(next, held[&uid].clone());
                            if let Some(flags) = flags {
                                state.flags.insert((user.clone(), dest.clone(), next), flags);
                            }
                            if moving {
                                state.folders.get_mut(&(user.clone(), folder.clone())).unwrap().remove(&uid);
                                state.flags.remove(&(user.clone(), folder.clone(), uid));
                            }
                            from.push(uid.to_string());
                            to.push(next.to_string());
                        }
                        if from.is_empty() {
                            format!("{tag} OK nothing to copy\r\n")
                        } else {
                            format!("* OK [COPYUID 7 {} {}] done\r\n{tag} OK done\r\n", from.join(","), to.join(","))
                        }
                    }
                };
                self.say(said.as_bytes())?;
            }
            "UID" if upper.starts_with("UID STORE ") => {
                let rest = &command["UID STORE ".len()..];
                let (set, rest) = rest.split_once(' ').unwrap_or((rest, ""));
                let adding = !rest.trim_start().starts_with('-');
                let named_flags: Vec<String> = rest
                    .split_once('(')
                    .map(|(_, list)| list.trim_end_matches(')').split_whitespace().map(str::to_owned).collect())
                    .unwrap_or_default();
                let folder = selected.clone().unwrap_or_default();
                {
                    let mut state = self.held();
                    let held = messages(&state);
                    for uid in named(set, &held) {
                        let flags = state.flags.entry((user.clone(), folder.clone(), uid)).or_default();
                        for flag in &named_flags {
                            if adding {
                                flags.insert(flag.clone());
                            } else {
                                flags.remove(flag);
                            }
                        }
                    }
                }
                self.say(format!("{tag} OK\r\n").as_bytes())?;
            }
            // Only the UIDs named, and only those marked deleted.
            "UID" if upper.starts_with("UID EXPUNGE ") => {
                let set = command["UID EXPUNGE ".len()..].trim().to_owned();
                let folder = selected.clone().unwrap_or_default();
                {
                    let mut state = self.held();
                    let held = messages(&state);
                    for uid in named(&set, &held) {
                        let key = (user.clone(), folder.clone(), uid);
                        if state.flags.get(&key).is_some_and(|f| f.contains("\\Deleted")) {
                            state.flags.remove(&key);
                            state.folders.get_mut(&(user.clone(), folder.clone())).unwrap().remove(&uid);
                        }
                    }
                }
                self.say(format!("{tag} OK\r\n").as_bytes())?;
            }
            _ => self.say(format!("{tag} OK\r\n").as_bytes())?,
        }
        Ok(true)
    }
}

#[derive(Debug)]
pub struct Frozen;

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(NOW * 1_000_000)
    }
}

/// `NOW` until a test moves it on, as it would a while passing: a try backed off comes due.
#[derive(Debug, Default)]
pub struct Moving(std::sync::atomic::AtomicI64);

impl Moving {
    pub fn shift(&self, seconds: i64) {
        self.0.fetch_add(seconds, std::sync::atomic::Ordering::SeqCst);
    }
}

impl Clock for Moving {
    fn now(&self) -> Moment {
        Moment::from_micros((NOW + self.0.load(std::sync::atomic::Ordering::SeqCst)) * 1_000_000)
    }
}

#[derive(Debug)]
pub struct Loopback;

impl Outside for Loopback {
    fn open(&self, _path: &Path) -> Result<(), Tripped> {
        Ok(())
    }

    fn reach(&self, host: &str) -> Result<(), Tripped> {
        if host == "127.0.0.1" { Ok(()) } else { Err(Tripped(host.to_owned())) }
    }

    fn run(&self, program: &str) -> Result<(), Tripped> {
        Err(Tripped(program.to_owned()))
    }
}

/// A Keychain that keeps what it is given.
#[derive(Debug, Default)]
pub struct Keys(Mutex<BTreeMap<String, String>>);

impl Keychain for Keys {
    fn find(&self, item: &str) -> Result<Option<Secret>, KeychainProblem> {
        Ok(self.0.lock().unwrap().get(item).map(|s| Secret::new(s.clone())))
    }

    fn put(&self, item: &str, secret: &Secret) -> Result<(), KeychainProblem> {
        self.0.lock().unwrap().insert(item.to_owned(), secret.expose().to_owned());
        Ok(())
    }

    fn delete(&self, item: &str) -> Result<bool, KeychainProblem> {
        Ok(self.0.lock().unwrap().remove(item).is_some())
    }
}

pub struct World {
    pub app: Arc<App>,
    pub imap: Imap,
    pub home: tempfile::TempDir,
    /// The app's clock, at `NOW` until a test moves it.
    pub clock: Arc<Moving>,
    /// The Keychain the app reads its accounts' passwords from.
    pub keychain: Arc<Keys>,
}

/// The app over a data directory of a database at the newest schema, its accounts on `imap`, with `more` of the
/// environment.
pub fn world(imap: Imap, emails: &[&str], more: &[(&str, &str)]) -> World {
    let keychain = Arc::new(Keys::default());
    for email in emails {
        keychain.put(&format!("account:{email}"), &Secret::new(PASSWORD.to_owned())).unwrap();
    }
    let home = tempfile::tempdir().unwrap();
    let accounts: Vec<Value> = emails
        .iter()
        .map(|email| {
            json!({"email": email, "type": "imap", "host": "127.0.0.1", "port": imap.port, "starttls": false,
                   "credential": {"keychain": format!("account:{email}")}})
        })
        .collect();
    std::fs::write(home.path().join("mailmind.config.json"), json!({"accounts": accounts}).to_string()).unwrap();
    database(home.path()).ready(&Frozen).unwrap();
    let hex = "5e".repeat(32);
    let mut pairs = vec![("MAILMIND_DB_KEY", hex.as_str())];
    pairs.extend_from_slice(more);
    let environment = Environment::from_pairs(pairs);
    let settings = Settings { home: home.path().to_path_buf() };
    let clock = Arc::new(Moving::default());
    let seams = Seams {
        clock: clock.clone(),
        outside: Arc::new(Loopback),
        keychain: keychain.clone(),
        sanitizer: Arc::new(mm_server::real::Sanitizing),
        mail_hosts: Arc::new(mm_server::mail_hosts::NoLookups),
        local_ai: Arc::new(mm_server::local_ai::NoLocalServers),
    };
    let dist: PathBuf = home.path().join("dist");
    let app = Arc::new(App::assemble(&environment, &settings, dist, 1, &seams).unwrap());
    World { app, imap, home, clock, keychain }
}

pub fn database(home: &Path) -> mm_store::Database {
    mm_store::Database::new(home.join("mailmind.db"), mm_store::DbKey::from_bytes(KEY), Arc::new(Loopback))
}

/// What is stored of a message: whether its whole body is, and its text.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct StoredBody {
    pub fetched: bool,
    pub text: Option<String>,
    /// A row in `bodies`.
    pub kept: bool,
}

impl World {
    /// What is stored of `account`'s message `uid` in `folder`.
    pub fn stored(&self, account: &str, folder: &str, uid: u32) -> StoredBody {
        database(self.home.path())
            .read(|c| {
                c.query_row(
                    "SELECT body_fetched, body_text, EXISTS(SELECT 1 FROM bodies b WHERE b.message_pk=m.pk) \
                     FROM messages m WHERE account=? AND folder=? AND uid=?",
                    (account, folder, uid),
                    |r| Ok(StoredBody { fetched: r.get::<_, i64>(0)? != 0, text: r.get(1)?, kept: r.get(2)? }),
                )
            })
            .unwrap()
    }

    /// The pk of `account`'s message `uid` in `folder`.
    pub fn pk(&self, account: &str, folder: &str, uid: u32) -> i64 {
        database(self.home.path())
            .read(|c| {
                c.query_row(
                    "SELECT pk FROM messages WHERE account=? AND folder=? AND uid=?",
                    (account, folder, uid),
                    |r| r.get(0),
                )
            })
            .unwrap()
    }

    /// Run `sql` on the database, as mail opened or stored elsewhere leaves it.
    pub fn write(&self, sql: &str) {
        database(self.home.path()).write(|c| c.execute_batch(sql)).unwrap();
    }
}
