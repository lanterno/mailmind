//! Leaving a sender's list (`mailmind.curation.domain.unsubscribe`, `application.unsubscribe` and
//! `content.mime.list_unsub`): what a sender's mail says of the way out, what that way is, and what trying it once
//! comes to. An attempt that got somewhere is latched by the sender's key, so a repeat never fires again, and anything
//! else stays retryable.
//!
//! Two ways out send something: a one-click POST to the list's host (RFC 8058), and a message from each account the
//! sender wrote to. Each goes through a port (`SafeHttp`, `SystemSend`), and a port that can't send yet says so before
//! the cleanup changes anything (`Unsubscribing::ready`).

use std::sync::{Arc, Mutex};
use std::time::Duration;

use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Moment};
use serde_json::{Map, Value, json};

use crate::cleanup_ports::{CleanupMail, Ids, ListHeaders, SafeHttp, SystemSend, UnsubRow};
use crate::text::{lower, strip};

/// Seconds a preview's confirm token stands.
pub const TOKEN_LIFE: i64 = 600;
/// What a confirm token is for, which a token minted for anything else isn't.
pub const TOKEN_PURPOSE: &str = "cleanup";
/// What is never fired twice; anything else stays retryable.
pub const TERMINAL: [&str; 3] = ["unsubscribed_oneclick", "mailto_sent", "needs_browser"];
/// At least this long between any two unsubscribes this process sends: politeness.
pub const MIN_GAP: Duration = Duration::from_secs(3);
const SUBJECT_MAX: usize = 200;
const TEXT_MAX: usize = 1000;
/// What `raw` keeps the List-Unsubscribe-Post header after (`_UNSUB_POST`).
const UNSUB_POST: &str = " | POST:";
const UNSUB_RAW_MAX: usize = 4000;

/// The way out of a list that a message's headers offer (`UnsubWay`). `method` is `one-click`, `mailto`, `http-page`
/// or `none`; `raw` is what `list_unsub_stored` reads it all back from.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct UnsubWay {
    pub method: &'static str,
    pub https_uri: Option<String>,
    pub mailto_uri: Option<String>,
    pub mailto_to: Option<String>,
    pub mailto_subject: Option<String>,
    pub mailto_body: Option<String>,
    pub raw: String,
}

impl UnsubWay {
    /// No way out (`NO_METHOD`).
    pub fn none() -> UnsubWay {
        UnsubWay {
            method: "none",
            https_uri: None,
            mailto_uri: None,
            mailto_to: None,
            mailto_subject: None,
            mailto_body: None,
            raw: String::new(),
        }
    }

    /// Whether a message offers any way out at all. One that doesn't ends what was kept: the list has stopped offering
    /// one.
    pub fn offers_a_way(&self) -> bool {
        self.method != "none"
    }
}

/// Python's `\s+` removed: Unicode's White_Space and the four information separators.
fn unspaced(text: &str) -> String {
    text.chars().filter(|c| !crate::text::is_space(*c)).collect()
}

/// The way to unsubscribe that a message's headers offer, from List-Unsubscribe (`lu`, unfolded) and
/// List-Unsubscribe-Post (`list_unsub`). RFC 2369 holds `<mailto:…>` and `<https://…>`; RFC 8058's one-click needs
/// both an https URI and `List-Unsubscribe-Post: List-Unsubscribe=One-Click`. A bare https URI is a landing page, for
/// a browser and never a POST. What is ambiguous is no way at all.
pub fn list_unsub(lu: &str, post: &str) -> UnsubWay {
    let lu = strip(lu);
    let lup = lower(&unspaced(post)).into_owned();
    let mut uris = Vec::new();
    let mut rest = lu;
    // re.findall(r"<([^>]+)>", lu): each `<` with at least one character and a `>` after it.
    while let Some(open) = rest.find('<') {
        let after = &rest[open + 1..];
        match after.find('>') {
            Some(0) => rest = after,
            Some(close) => {
                uris.push(unspaced(&after[..close]));
                rest = &after[close + 1..];
            }
            None => break,
        }
    }
    let https_uri = uris.iter().find(|u| lower(u).starts_with("https://")).cloned();
    let mailto_uri = uris.iter().find(|u| lower(u).starts_with("mailto:")).cloned();
    let method = if https_uri.is_some() && lup == "list-unsubscribe=one-click" {
        "one-click"
    } else if mailto_uri.is_some() {
        "mailto"
    } else if https_uri.is_some() {
        "http-page"
    } else {
        "none"
    };
    let (mailto_to, mailto_subject, mailto_body) = match &mailto_uri {
        Some(uri) => mailto_parts(uri),
        None => (None, None, None),
    };
    let mut raw = lu.to_owned();
    if !lup.is_empty() {
        raw.push_str(UNSUB_POST);
        raw.push_str(&lup);
    }
    UnsubWay {
        method,
        https_uri,
        mailto_uri,
        mailto_to,
        mailto_subject,
        mailto_body,
        raw: raw.chars().take(UNSUB_RAW_MAX).collect(),
    }
}

/// What `list_unsub` said, from the `raw` it gave.
pub fn list_unsub_stored(raw: &str) -> UnsubWay {
    let (lu, post) = raw.split_once(UNSUB_POST).unwrap_or((raw, ""));
    list_unsub(lu, post)
}

/// The way out a raw message's headers offer (`parse_list_unsub`).
pub fn parse_list_unsub(raw: &[u8]) -> UnsubWay {
    list_unsub_stored(&mm_content::mime::fields(raw).unsub_raw)
}

/// What a message's headers say, with no way out said one way however they said it.
pub fn said(parsed: UnsubWay) -> UnsubWay {
    if parsed.offers_a_way() { parsed } else { UnsubWay::none() }
}

/// Python's `urllib.parse.unquote`: each `%XX` a byte, the bytes read as UTF-8 with what isn't replaced.
fn unquote(text: &str) -> String {
    if !text.contains('%') {
        return text.to_owned();
    }
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%'
            && let Some(byte) = bytes.get(i + 1..i + 3).and_then(|h| std::str::from_utf8(h).ok()).and_then(|h| {
                h.bytes().all(|b| b.is_ascii_hexdigit()).then(|| u8::from_str_radix(h, 16).ok()).flatten()
            })
        {
            out.push(byte);
            i += 3;
            continue;
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// The parts of a URL `urllib.parse.urlsplit` reads: (netloc, path, query), its fragment left off.
fn split_url(uri: &str) -> (&str, &str, &str) {
    let after_scheme = uri.split_once(':').map_or(uri, |(_, rest)| rest);
    let (netloc, rest) = match after_scheme.strip_prefix("//") {
        Some(rest) => {
            let end = rest.find(['/', '?', '#']).unwrap_or(rest.len());
            (&rest[..end], &rest[end..])
        }
        None => ("", after_scheme),
    };
    let rest = rest.split_once('#').map_or(rest, |(before, _)| before);
    let (path, query) = rest.split_once('?').unwrap_or((rest, ""));
    (netloc, path, query)
}

/// Who a `mailto:` writes to, and the subject and body it asks for (`urlparse` and `parse_qs`).
fn mailto_parts(uri: &str) -> (Option<String>, Option<String>, Option<String>) {
    let (_, path, query) = split_url(uri);
    let to = Some(strip(&unquote(path)).to_owned()).filter(|t| !t.is_empty());
    let (mut subject, mut body) = (None, None);
    for pair in query.split('&') {
        let Some((name, value)) = pair.split_once('=') else { continue };
        if value.is_empty() {
            continue;
        }
        let name = unquote(&name.replace('+', " "));
        let value = unquote(&value.replace('+', " "));
        match name.as_str() {
            "subject" if subject.is_none() => subject = Some(value),
            "body" if body.is_none() => body = Some(value),
            _ => {}
        }
    }
    (to, subject, body)
}

/// The host an https URI names, lower-case, as `urlparse(uri).hostname` reads it; None where it names none.
fn hostname(uri: &str) -> Option<String> {
    let (netloc, _, _) = split_url(uri);
    let info = netloc.rsplit_once('@').map_or(netloc, |(_, info)| info);
    let host = match info.split_once('[') {
        Some((_, bracketed)) => bracketed.split_once(']')?.0,
        None => info.split_once(':').map_or(info, |(host, _)| host),
    };
    if host.is_empty() {
        return None;
    }
    let (name, zone) = host.split_once('%').map_or((host, None), |(name, zone)| (name, Some(zone)));
    let mut out = lower(name).into_owned();
    if let Some(zone) = zone {
        out.push('%');
        out.push_str(zone);
    }
    Some(out)
}

/// What a person is shown before confirming: the way, and only where it goes.
pub fn preview(info: &UnsubWay) -> Value {
    let mut shown = Map::new();
    shown.insert("method".into(), json!(info.method));
    if matches!(info.method, "one-click" | "http-page") {
        shown.insert("https_host".into(), json!(hostname(info.https_uri.as_deref().unwrap_or_default())));
    }
    if info.method == "mailto" {
        shown.insert("mailto_to".into(), json!(info.mailto_to));
    }
    Value::Object(shown)
}

/// What a preview's confirm token stands for: who, what would go out and where. A list that changes its way out
/// since then needs a fresh look.
pub fn preview_said(from_addr: &str, info: &UnsubWay) -> [Option<String>; 3] {
    let target = if info.method == "mailto" { info.mailto_to.clone() } else { info.https_uri.clone() };
    [Some(lower(from_addr).into_owned()), Some(info.method.to_owned()), target]
}

/// What the attempts on a sender are kept under.
pub fn attempt_key(from_addr: &str) -> String {
    format!("unsub:{from_addr}")
}

/// What trying a way out came to: its `status`, which `method` it took, the server's answer to a one-click and a page
/// for the person to open.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Attempt {
    pub status: String,
    pub method: String,
    pub http_status: Option<i64>,
    pub browser_url: Option<String>,
}

impl Attempt {
    fn new(status: &str, method: &str) -> Attempt {
        Attempt { status: status.to_owned(), method: method.to_owned(), http_status: None, browser_url: None }
    }

    /// Whether it is never tried again.
    pub fn latched(&self) -> bool {
        TERMINAL.contains(&self.status.as_str())
    }
}

/// A read-only process sends nothing outside; not latched, so retryable.
pub fn read_only() -> Attempt {
    Attempt::new("read_only", "none")
}

/// An unsubscribe that didn't go: nobody asked, or nobody showed a preview's good token. Block and sweep still stand.
pub fn not_tried(asked: bool) -> Attempt {
    Attempt::new(if asked { "no_token" } else { "skipped" }, "none")
}

/// An attempt as the log keeps it: its status and method.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Logged {
    pub status: String,
    pub method: String,
}

/// What an attempt already latched answers, with nothing fired, or None if there is none. A page can be opened again.
pub fn repeat_of(prior: Option<&Logged>, info: &UnsubWay) -> Option<Attempt> {
    let prior = prior.filter(|p| TERMINAL.contains(&p.status.as_str()))?;
    let mut again = Attempt::new(&prior.status, &prior.method);
    if prior.method == "http-page" {
        again.browser_url = info.https_uri.clone();
    }
    Some(again)
}

pub fn after_one_click(ok: bool, http_status: Option<i64>) -> Attempt {
    Attempt { http_status, ..Attempt::new(if ok { "unsubscribed_oneclick" } else { "failed" }, "one-click") }
}

pub fn after_mailto(sent: bool) -> Attempt {
    Attempt::new(if sent { "mailto_sent" } else { "failed" }, "mailto")
}

/// A way that has nothing to send: a page is for the person to open, and otherwise there's no way at all.
pub fn without_sending(info: &UnsubWay) -> Attempt {
    if info.method == "http-page" {
        return Attempt { browser_url: info.https_uri.clone(), ..Attempt::new("needs_browser", "http-page") };
    }
    Attempt::new("no_method", info.method)
}

/// A mailto: way out as it may go: to one plain address, under a subject on one line, with a short text.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Mailto {
    pub to: String,
    pub subject: String,
    pub text: String,
}

/// One plain address: no blank and none of `@<>,;:"()[]\` either side of its one `@`.
fn plain_address(to: &str) -> bool {
    let plain = |part: &str| {
        !part.is_empty() && !part.chars().any(|c| crate::text::is_space(c) || "@<>,;:\"()[]\\".contains(c))
    };
    to.split_once('@').is_some_and(|(local, domain)| plain(local) && plain(domain))
}

/// What a list's mailto: asks to send, or None if it doesn't name one plain address, and then nothing is sent.
pub fn mailto_of(info: &UnsubWay) -> Option<Mailto> {
    let to = info.mailto_to.clone().unwrap_or_default();
    if !plain_address(&to) {
        return None;
    }
    let subject = info.mailto_subject.as_deref().filter(|s| !s.is_empty()).unwrap_or("unsubscribe");
    let subject: String = subject.replace(['\r', '\n'], " ").chars().take(SUBJECT_MAX).collect();
    let text = info.mailto_body.as_deref().filter(|s| !s.is_empty()).unwrap_or("Please unsubscribe me.");
    Some(Mailto { to, subject, text: breaks_kept_short(text).chars().take(TEXT_MAX).collect() })
}

/// `re.sub(r"[\r\n]{3,}", "\n\n", text)`.
fn breaks_kept_short(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut run = String::new();
    for c in text.chars() {
        if c == '\r' || c == '\n' {
            run.push(c);
            continue;
        }
        out.push_str(if run.chars().count() >= 3 { "\n\n" } else { &run });
        run.clear();
        out.push(c);
    }
    out.push_str(if run.chars().count() >= 3 { "\n\n" } else { &run });
    out
}

/// What the sender's newest message that can say says, newest first through `rows`: from what ingest kept of its
/// headers, else from its stored body. None if no row can say. `body_of(pk)` gives a stored body or None, and
/// `remember(pk, parsed)` keeps what a body said.
pub fn stored<E>(
    rows: &[UnsubRow],
    mut body_of: impl FnMut(i64) -> Result<Option<Vec<u8>>, E>,
    mut remember: impl FnMut(i64, &UnsubWay),
) -> Result<Option<UnsubWay>, E> {
    for r in rows {
        let method = r.list_unsub_method.as_deref().unwrap_or_default();
        if method == "none" {
            // An answer kept that there is none: nothing is read again.
            return Ok(Some(UnsubWay::none()));
        }
        if !method.is_empty() {
            let parsed = list_unsub_stored(r.list_unsub_raw.as_deref().unwrap_or_default());
            if parsed.offers_a_way() {
                return Ok(Some(parsed));
            }
        }
        let Some(raw) = body_of(r.pk)? else { continue };
        let parsed = parse_list_unsub(&raw);
        if method.is_empty() {
            remember(r.pk, &parsed);
        }
        return Ok(Some(said(parsed)));
    }
    Ok(None)
}

/// A message as it goes from `from` to a list's mailto: (`email.message.EmailMessage`, written with SMTP's CRLF):
/// the headers Python writes, in its order, and the text as 7bit, or 8bit where it isn't ASCII.
pub fn mailto_message(from: &str, mailto: &Mailto, date: &str, message_id: &str) -> Vec<u8> {
    let subject = if mailto.subject.is_ascii() {
        mailto.subject.clone()
    } else {
        format!("=?utf-8?b?{}?=", base64(mailto.subject.as_bytes()))
    };
    let encoding = if mailto.text.is_ascii() { "7bit" } else { "8bit" };
    let mut text = mailto.text.replace("\r\n", "\n").replace('\r', "\n");
    if !text.ends_with('\n') {
        text.push('\n');
    }
    let text = text.replace('\n', "\r\n");
    format!(
        "From: {from}\r\nTo: {to}\r\nSubject: {subject}\r\nDate: {date}\r\nMessage-ID: {message_id}\r\n\
         Auto-Submitted: auto-generated\r\nContent-Type: text/plain; charset=\"utf-8\"\r\n\
         Content-Transfer-Encoding: {encoding}\r\nMIME-Version: 1.0\r\n\r\n{text}",
        to = mailto.to,
    )
    .into_bytes()
}

fn base64(bytes: &[u8]) -> String {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let b = [chunk[0], chunk.get(1).copied().unwrap_or(0), chunk.get(2).copied().unwrap_or(0)];
        let word = (u32::from(b[0]) << 16) | (u32::from(b[1]) << 8) | u32::from(b[2]);
        for i in 0..4 {
            if i <= chunk.len() {
                out.push(char::from(ALPHABET[((word >> (18 - 6 * i)) & 63) as usize]));
            } else {
                out.push('=');
            }
        }
    }
    out
}

/// `email.utils.format_datetime` of a moment in UTC: `Mon, 02 Nov 2026 12:00:00 +0000`.
pub fn mail_date(at: Moment) -> String {
    const DAYS: [&str; 7] = ["Thu", "Fri", "Sat", "Sun", "Mon", "Tue", "Wed"];
    const MONTHS: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    let seconds = at.whole_seconds();
    let days = seconds.div_euclid(86_400);
    let of_day = seconds.rem_euclid(86_400);
    let (year, month, day) = mm_kernel::when::civil(days);
    let weekday = DAYS[usize::try_from(days.rem_euclid(7)).unwrap_or(0)];
    let month = MONTHS[(month as usize).saturating_sub(1).min(11)];
    format!(
        "{weekday}, {day:02} {month} {year:04} {:02}:{:02}:{:02} +0000",
        of_day / 3600,
        of_day % 3600 / 60,
        of_day % 60
    )
}

/// Politeness: at least MIN_GAP between any two unsubscribes this process sends.
#[derive(Debug, Default)]
struct Pace {
    last: Mutex<Option<Moment>>,
}

impl Pace {
    fn wait_turn(&self, clock: &dyn Clock) {
        let Ok(mut last) = self.last.lock() else { return };
        if let Some(then) = *last {
            let gap = i64::try_from(MIN_GAP.as_micros()).unwrap_or(i64::MAX);
            let wait = gap - (clock.now().micros() - then.micros());
            if wait > 0 {
                std::thread::sleep(Duration::from_micros(u64::try_from(wait.min(gap)).unwrap_or(0)));
            }
        }
        *last = Some(clock.now());
    }
}

/// What trying a sender's way out would do, decided before anything is changed.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Next {
    /// What it answers with nothing fired and nothing new to keep: a read-only process, or an attempt already latched.
    Answered(Attempt),
    /// What it comes to with nothing to send, kept if it latches: a page to open, no way, or no plain address.
    Unsent(Attempt),
    /// A one-click POST to this URL.
    OneClick(String),
    /// A message to the list from each account the sender wrote to.
    Mailto(Mailto),
}

/// What resolving a sender's way out found: the way, and the accounts the sender wrote to.
pub type Resolved = (UnsubWay, Vec<String>);

#[derive(Debug)]
pub struct Unsubscribing {
    mail: Arc<dyn CleanupMail>,
    headers: Arc<dyn ListHeaders>,
    http: Arc<dyn SafeHttp>,
    send: Arc<dyn SystemSend>,
    ids: Arc<dyn Ids>,
    clock: Arc<dyn Clock>,
    writes_allowed: bool,
    pace: Pace,
}

/// What Unsubscribing is made of.
#[derive(Debug)]
pub struct UnsubscribingParts {
    pub mail: Arc<dyn CleanupMail>,
    pub headers: Arc<dyn ListHeaders>,
    pub http: Arc<dyn SafeHttp>,
    pub send: Arc<dyn SystemSend>,
    pub ids: Arc<dyn Ids>,
    pub clock: Arc<dyn Clock>,
    /// False in a read-only process, which sends nothing outside.
    pub writes_allowed: bool,
}

impl Unsubscribing {
    pub fn new(parts: UnsubscribingParts) -> Unsubscribing {
        let UnsubscribingParts { mail, headers, http, send, ids, clock, writes_allowed } = parts;
        Unsubscribing { mail, headers, http, send, ids, clock, writes_allowed, pace: Pace::default() }
    }

    /// Ask the server for one message's unsubscribe headers alone: mail from before ingest kept them, with no body
    /// stored. Keeps what it hears; None if it can't say now.
    fn read_live(&self, row: &UnsubRow) -> Result<Option<UnsubWay>, Fault> {
        let Some(raw) = self.headers.read(row) else { return Ok(None) };
        let parsed = parse_list_unsub(&raw);
        self.mail.remember_unsub(row.pk, &parsed)?;
        Ok(Some(said(parsed)))
    }

    /// The way out of a sender's list: the newest message's List-Unsubscribe, from what is stored, else from the
    /// server for the newest message alone, else (a sweep took the sender's mail, and this is a second try) from what
    /// was last read. With the accounts it wrote to.
    pub fn resolve(&self, from_addr: &str) -> Result<Resolved, Fault> {
        let (rows, accounts, mut info) = self.mail.unsub_stored(from_addr)?;
        if info.is_none()
            && let Some(newest) = rows.first()
        {
            info = self.read_live(newest)?;
        }
        if let Some(info) = info {
            self.mail.keep_unsub(from_addr, &info, &accounts)?;
            return Ok((info, accounts));
        }
        if let Some((raw, kept_accounts)) = self.mail.kept_unsub(from_addr)? {
            return Ok((list_unsub_stored(&raw), kept_accounts));
        }
        Ok((UnsubWay::none(), accounts))
    }

    /// What trying the way `resolved` names would do.
    pub fn next(&self, from_addr: &str, resolved: &Resolved) -> Result<Next, Fault> {
        if !self.writes_allowed {
            return Ok(Next::Answered(read_only()));
        }
        let (info, _) = resolved;
        if let Some(again) = repeat_of(self.mail.attempt(&attempt_key(from_addr))?.as_ref(), info) {
            return Ok(Next::Answered(again));
        }
        Ok(match info.method {
            "one-click" => Next::OneClick(info.https_uri.clone().unwrap_or_default()),
            "mailto" => match mailto_of(info) {
                Some(mailto) => Next::Mailto(mailto),
                None => Next::Unsent(after_mailto(false)),
            },
            _ => Next::Unsent(without_sending(info)),
        })
    }

    /// Whether what `next` would send can be sent from this server.
    pub fn ready(&self, next: &Next) -> bool {
        match next {
            Next::OneClick(_) => self.http.ready(),
            Next::Mailto(_) => self.send.ready(),
            Next::Answered(_) | Next::Unsent(_) => true,
        }
    }

    /// Send the mailto: from each affected account (the list knows a person by their From), through the outbox, so a
    /// server that's away only makes it wait and it's filed in Sent like anything else they send. Where it goes from
    /// comes from the config, by the account's address, never from the request.
    fn mailto(&self, mailto: &Mailto, accounts: &[String], key: &str) -> bool {
        let mut sent = 0;
        let date = mail_date(self.clock.now());
        for account in accounts {
            let domain = account.rsplit_once('@').map(|(_, d)| d).filter(|d| !d.is_empty()).unwrap_or("localhost");
            let message_id = format!("<{}.{}@{domain}>", self.clock.now().micros() / 10_000, self.ids.hex(8));
            let raw = mailto_message(account, mailto, &date, &message_id);
            let keyed = format!("{key}:{account}");
            // A removed or stale account: skip it, not the cleanup.
            if self.send.send(account, &raw, &message_id, &mailto.to, &mailto.subject, &keyed).is_ok() {
                sent += 1;
            }
        }
        sent > 0
    }

    /// Try what `next` (from `next`, for the same `resolved`) says.
    pub fn attempt(&self, from_addr: &str, resolved: &Resolved, next: Next) -> Result<Attempt, Fault> {
        let key = attempt_key(from_addr);
        let tried = match next {
            Next::Answered(attempt) => return Ok(attempt),
            Next::Unsent(attempt) => attempt,
            Next::OneClick(url) => {
                self.pace.wait_turn(&*self.clock);
                let (ok, status) = self.http.one_click_post(&url);
                after_one_click(ok, status)
            }
            Next::Mailto(mailto) => {
                self.pace.wait_turn(&*self.clock);
                after_mailto(self.mailto(&mailto, &resolved.1, &key))
            }
        };
        if tried.latched() {
            self.mail.record_attempt(from_addr, &tried, &key, &resolved.1)?;
        }
        Ok(tried)
    }
}
