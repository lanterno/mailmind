//! MailMind's authenticated connection to the GentleMail REST API.
use mm_kernel::seams::{Outside, Secret};
use mm_net::http::{self, Endpoint, Response};
use serde::Deserialize;
use std::time::Duration;
use zeroize::Zeroizing;

pub(crate) struct Client {
    endpoint: Endpoint,
    key: Secret,
}

#[derive(Deserialize)]
struct Page<T> {
    data: Vec<T>,
}

#[derive(Deserialize)]
struct Mailbox {
    address: String,
    active: bool,
    permissions: Vec<String>,
}

impl Client {
    pub(crate) fn for_host(host: Option<&str>, key: Secret) -> Result<Self, &'static str> {
        let host = host
            .filter(|h| !h.is_empty() && h.bytes().all(|c| c.is_ascii_alphanumeric() || b".-".contains(&c)))
            .ok_or("InvalidGentleMailOrigin")?;
        Self::new(&format!("https://{host}"), key)
    }

    pub(crate) fn new(origin: &str, key: Secret) -> Result<Self, &'static str> {
        let endpoint = Endpoint::parse(origin).ok_or("InvalidGentleMailOrigin")?;
        if !endpoint.tls || !endpoint.base.is_empty() || origin.contains(['?', '#']) {
            return Err("InvalidGentleMailOrigin");
        }
        if !key.expose().starts_with("gm_")
            || !key.expose().bytes().all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
        {
            return Err("InvalidGentleMailKey");
        }
        Ok(Self { endpoint, key })
    }

    fn get(&self, outside: &dyn Outside, path: &str, timeout: Duration) -> Result<Response, &'static str> {
        outside.reach(&self.endpoint.host).map_err(|_| "GentleMailUnreachable")?;
        let bearer = Zeroizing::new(format!("Bearer {}", self.key.expose()));
        let request = Zeroizing::new(http::request(
            "GET",
            path,
            &self.endpoint.host_header(),
            &[("Authorization", &bearer)],
            None,
        ));
        let response = self.endpoint.send(&request, timeout).map_err(|_| "GentleMailUnreachable")?;
        match response.status {
            200 => Ok(response),
            401 | 403 => Err("GentleMailAccessDenied"),
            _ => Err("GentleMailRequestFailed"),
        }
    }

    pub(crate) fn verify(&self, outside: &dyn Outside, address: &str, timeout: Duration) -> Result<(), &'static str> {
        let response = self.get(outside, "/api/v1/mailboxes", timeout)?;
        let page: Page<Mailbox> = serde_json::from_slice(&response.body).map_err(|_| "InvalidGentleMailResponse")?;
        page.data
            .iter()
            .any(|m| {
                m.active
                    && m.address.eq_ignore_ascii_case(address)
                    && ["read", "send"].iter().all(|p| m.permissions.iter().any(|v| v == p))
            })
            .then_some(())
            .ok_or("GentleMailAccessDenied")
    }
}

use base64::{Engine, engine::general_purpose::STANDARD};
use mail_builder::MessageBuilder;
use mm_accounts::{Account, Directory};
use mm_compose::sending_ports::NotSent;
use mm_mailbox::session::ServerError;
use serde_json::json;
use sha2::{Digest, Sha256};
const INVALID: ServerError = ServerError::Refused("InvalidGentleMailResponse");
#[derive(Deserialize)]
struct Item<T> {
    data: T,
}
#[derive(Deserialize)]
struct Address {
    address: String,
    name: Option<String>,
}
impl Address {
    fn pair(&self) -> (String, String) {
        (clean(self.name.as_deref().unwrap_or("")), clean(&self.address))
    }
}
fn clean(s: &str) -> String {
    s.chars().filter(|c| !c.is_control()).collect()
}
#[derive(Deserialize)]
struct Attachment {
    id: String,
    filename: String,
    content_type: String,
}
#[derive(Deserialize)]
pub(crate) struct Message {
    pub id: String,
    pub folder: String,
    pub is_read: bool,
    date: String,
    subject: String,
    from: Address,
    to: Vec<Address>,
    cc: Vec<Address>,
    message_id: Option<String>,
    in_reply_to: Option<String>,
    reply_to: Option<String>,
    text: Option<String>,
    html: Option<String>,
    text_truncated: bool,
    attachments: Vec<Attachment>,
}
impl Message {
    pub fn timestamp(&self) -> Result<i64, ServerError> {
        mail_parser::DateTime::parse_rfc3339(&self.date).map(|d| d.to_timestamp()).ok_or(INVALID)
    }
    fn builder(&self) -> Result<MessageBuilder<'static>, ServerError> {
        let mut b = MessageBuilder::new()
            .date(self.timestamp()?)
            .from(self.from.pair())
            .to(self.to.iter().map(Address::pair).collect::<Vec<_>>())
            .subject(clean(&self.subject))
            .message_id(bare_message_id(self.message_id.as_deref().unwrap_or(&self.id)));
        if !self.cc.is_empty() {
            b = b.cc(self.cc.iter().map(Address::pair).collect::<Vec<_>>());
        }
        if let Some(reply) = &self.in_reply_to {
            b = b.in_reply_to(bare_message_id(reply));
        }
        if let Some(reply) = &self.reply_to {
            b = b.reply_to(clean(reply));
        }
        Ok(b)
    }
    pub fn headers(&self) -> Result<Vec<u8>, ServerError> {
        self.builder()?.text_body("").write_to_vec().map_err(|_| INVALID)
    }
}
// mail-builder supplies the outer angle brackets when serializing an RFC message ID.
fn bare_message_id(value: &str) -> String {
    clean(value.trim().trim_matches(['<', '>']))
}

fn segment(id: &str) -> Result<&str, ServerError> {
    if !id.is_empty() && id.bytes().all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b)) {
        Ok(id)
    } else {
        Err(INVALID)
    }
}
fn escaped(address: &str) -> String {
    address
        .bytes()
        .map(|b| if b.is_ascii_alphanumeric() { char::from(b).to_string() } else { format!("%{b:02X}") })
        .collect()
}
pub(crate) struct MailboxSession {
    client: Client,
    address: String,
    ids: rusqlite::Connection,
}
impl MailboxSession {
    pub fn open(account: &Account, key: Secret, outside: &dyn Outside, timeout: Duration) -> Result<Self, ServerError> {
        let client = Client::for_host(account.host.as_str(), key).map_err(ServerError::Refused)?;
        client.verify(outside, &account.email, timeout).map_err(ServerError::Refused)?;
        let root = std::path::PathBuf::from(std::env::var_os("HOME").ok_or(INVALID)?)
            .join("Library/Application Support/MailMind/GentleMail");
        std::fs::create_dir_all(&root).map_err(|_| INVALID)?;
        let identity = format!("{}\n{}", client.endpoint.host, account.email);
        let name: String = Sha256::digest(identity.as_bytes()).iter().map(|b| format!("{b:02x}")).collect();
        let path = root.join(format!("{name}.sqlite3"));
        let ids = rusqlite::Connection::open(path).map_err(|_| INVALID)?;
        ids.execute_batch(
            "CREATE TABLE IF NOT EXISTS uids(uid INTEGER PRIMARY KEY AUTOINCREMENT, message TEXT NOT NULL UNIQUE); CREATE TABLE IF NOT EXISTS replies(header_digest BLOB PRIMARY KEY, remote_id TEXT NOT NULL)",
        )
        .map_err(|_| INVALID)?;
        Ok(Self { client, address: account.email.clone(), ids })
    }
    pub fn snapshot(&self, outside: &dyn Outside, timeout: Duration) -> Result<Vec<(u32, Message)>, ServerError> {
        let mut all = Vec::new();
        for folder in ["inbox", "sent", "archive", "spam", "trash"] {
            let path = format!("/api/v1/messages?mailbox={}&folder={folder}&limit=100", escaped(&self.address));
            let r = self.client.get(outside, &path, timeout).map_err(ServerError::Unreachable)?;
            let page: Page<Message> = serde_json::from_slice(&r.body).map_err(|_| INVALID)?;
            if page.data.len() == 100 {
                return Err(ServerError::Refused("GentleMailPaginationRequired"));
            }
            all.extend(page.data);
        }
        all.sort_by(|a, b| (&a.date, &a.id).cmp(&(&b.date, &b.id)));
        let tx = self.ids.unchecked_transaction().map_err(|_| INVALID)?;
        let mut result = Vec::new();
        for m in all {
            segment(&m.id)?;
            tx.execute(
                "INSERT INTO uids(message) SELECT ?1 WHERE NOT EXISTS(SELECT 1 FROM uids WHERE message=?1)",
                [&m.id],
            )
            .map_err(|_| INVALID)?;
            let uid =
                tx.query_row("SELECT uid FROM uids WHERE message=?", [&m.id], |r| r.get(0)).map_err(|_| INVALID)?;
            if let Some(mid) = &m.message_id {
                let digest = Sha256::digest(mid.trim_matches(['<', '>']).as_bytes());
                tx.execute(
                    "INSERT OR IGNORE INTO replies(header_digest,remote_id) VALUES (?,?)",
                    rusqlite::params![digest.as_slice(), m.id],
                )
                .map_err(|_| INVALID)?;
            }
            result.push((uid, m));
        }
        tx.commit().map_err(|_| INVALID)?;
        Ok(result)
    }
    pub fn body(&self, outside: &dyn Outside, id: &str, timeout: Duration) -> Result<Vec<u8>, ServerError> {
        let path = format!("/api/v1/messages/{}", segment(id)?);
        let r = self.client.get(outside, &path, timeout).map_err(ServerError::Unreachable)?;
        let Item { data: m }: Item<Message> = serde_json::from_slice(&r.body).map_err(|_| INVALID)?;
        if m.text_truncated && m.html.is_none() {
            return Err(ServerError::Refused("GentleMailBodyTruncated"));
        }
        let mut b = m.builder()?;
        if let Some(text) = m.text {
            b = b.text_body(text)
        }
        if let Some(html) = m.html {
            b = b.html_body(html)
        }
        for a in m.attachments {
            let path = format!("/api/v1/attachments/{}", segment(&a.id)?);
            let part = self.client.get(outside, &path, timeout).map_err(ServerError::Unreachable)?;
            b = b.attachment(clean(&a.content_type), clean(&a.filename), part.body);
        }
        b.write_to_vec().map_err(|_| INVALID)
    }
}
pub(crate) fn send(
    account: &Account,
    directory: &Directory,
    outside: &dyn Outside,
    raw: &[u8],
    recipients: &[String],
) -> Result<(), NotSent> {
    let rejected = NotSent { said: "GentleMail rejected the message", permanent: true, unanswered: false };
    let uncertain = NotSent { said: "GentleMail delivery unconfirmed", permanent: false, unanswered: true };
    let key = directory.password(account).map_err(|_| rejected)?;
    let session = MailboxSession::open(account, key, outside, Duration::from_secs(20)).map_err(|_| rejected)?;
    let client = &session.client;
    let m = mm_content::mime::parse(raw);
    let fields = mm_content::mime::fields(raw);
    let bcc: Vec<_> = recipients.iter().filter(|r| !fields.recipient_addrs.contains(&r.to_ascii_lowercase())).collect();
    let mut files = Vec::new();
    for a in &m.attachments {
        let (bytes, _) = mm_content::mime::extract_part(raw, a.part_id).ok_or(rejected)?;
        files.push(json!({"filename":a.filename,"content_type":a.content_type,"content":STANDARD.encode(bytes)}));
    }
    let parse = |v: &str| {
        mail_parser::MessageParser::default()
            .parse(format!("To: {v}\r\n\r\n").as_bytes())
            .and_then(|m| {
                m.to().map(|a| a.mailboxes().filter_map(|a| a.address().map(str::to_owned)).collect::<Vec<_>>())
            })
            .unwrap_or_default()
    };
    let mut payload = json!({"from":account.email,"to":parse(&m.to),"cc":parse(&m.cc),"bcc":bcc,"subject":fields.subject,"text":m.text,"html":m.html,"attachments":files});
    if !fields.in_reply_to.is_empty() {
        use rusqlite::OptionalExtension;
        let digest = Sha256::digest(fields.in_reply_to.trim_matches(['<', '>']).as_bytes());
        let remote: Option<String> = session
            .ids
            .query_row("SELECT remote_id FROM replies WHERE header_digest=?", [digest.as_slice()], |r| r.get(0))
            .optional()
            .map_err(|_| rejected)?;
        if let Some(id) = remote {
            payload["reply_to_message_id"] = id.into();
        }
    }
    let body = Zeroizing::new(payload.to_string());
    outside.reach(&client.endpoint.host).map_err(|_| rejected)?;
    let auth = Zeroizing::new(format!("Bearer {}", client.key.expose()));
    let req = Zeroizing::new(http::request(
        "POST",
        "/api/v1/send",
        &client.endpoint.host_header(),
        &[("Authorization", &auth), ("Content-Type", "application/json")],
        Some(body.as_bytes()),
    ));
    let r = client.endpoint.send(&req, Duration::from_secs(45)).map_err(|_| uncertain)?;
    match r.status {
        200..=299 => Ok(()),
        400..=499 => Err(rejected),
        _ => Err(uncertain),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reconstructs_ids_with_one_pair_of_brackets() {
        let message = MessageBuilder::new()
            .message_id(bare_message_id("<sample@example.test>"))
            .in_reply_to(bare_message_id("parent@example.test"))
            .text_body("test")
            .write_to_vec()
            .unwrap();
        let headers = String::from_utf8(message).unwrap();
        assert!(headers.contains("Message-ID: <sample@example.test>"));
        assert!(headers.contains("In-Reply-To: <parent@example.test>"));
    }
}
