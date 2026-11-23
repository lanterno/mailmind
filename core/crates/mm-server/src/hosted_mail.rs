//! HodHod HTTPS transport. The normal mailbox and composer consume the same ports as IMAP and SMTP.
use std::borrow::Cow;
use std::collections::BTreeMap;
use std::time::{Duration, Instant};

use base64::{Engine, engine::general_purpose::STANDARD};
use mm_accounts::{Account, Directory};
use mm_compose::sending_ports::NotSent;
use mm_kernel::errors::Fault;
use mm_kernel::seams::{Outside, Secret};
use mm_mailbox::ports::{MailSession, SessionError};
use mm_mailbox::roles::Folder;
use mm_mailbox::session::{Headers, MailServer, NO_SUCH_FOLDER, Search, Selected, ServerError};
use mm_net::http::{self, Endpoint, Response};
use ring::aead::{AES_256_GCM, Aad, LessSafeKey, Nonce, UnboundKey};
use security_framework::key::{Algorithm, SecKey};
use security_framework::os::macos::import_export::{ImportOptions, SecItems};
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use zeroize::Zeroizing;

const INVALID: ServerError = ServerError::Refused("InvalidHostedMailbox");
const UNREACHABLE: ServerError = ServerError::Unreachable("HostedMailboxUnreachable");
const UNSUPPORTED: ServerError = ServerError::Refused("Unsupported");
const UNKNOWN_UID: ServerError = ServerError::Refused("UnknownMessage");
const CORRUPT: ServerError = ServerError::Failed(Fault::new("InvalidHostedEnvelope"));

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct ProfileFile<'a> {
    #[serde(borrow)]
    origin: Cow<'a, str>,
    #[serde(borrow)]
    token: Cow<'a, str>,
    #[serde(borrow)]
    mailbox: Cow<'a, str>,
    #[serde(borrow)]
    private_key: Cow<'a, str>,
}

struct Profile {
    endpoint: Endpoint,
    token: Secret,
    mailbox: String,
    key: SecKey,
}

fn identifier(text: &str) -> bool {
    !text.is_empty() && text.len() <= 64 && text.bytes().all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
}

fn digest(text: &str) -> bool {
    text.len() == 64 && text.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn private_key(encoded: &str) -> Option<SecKey> {
    if encoded.len() > 12_000 || !encoded.bytes().all(|b| b.is_ascii_alphanumeric() || b"+/=".contains(&b)) {
        return None;
    }
    let pem = Zeroizing::new(format!("-----BEGIN RSA PRIVATE KEY-----\n{encoded}\n-----END RSA PRIVATE KEY-----\n"));
    let mut items = SecItems::default();
    ImportOptions::new().filename("mailbox.pem").items(&mut items).import(pem.as_bytes()).ok()?;
    if items.keys.len() != 1 {
        return None;
    }
    items.keys.pop()
}

impl Profile {
    fn read(secret: &Secret) -> Result<Self, ServerError> {
        let raw = secret.expose();
        if raw.len() > 64_000 {
            return Err(INVALID);
        }
        let file: ProfileFile<'_> = serde_json::from_str(raw).map_err(|_| INVALID)?;
        let endpoint = Endpoint::parse(&file.origin).ok_or(INVALID)?;
        let Some((_, authority)) = file.origin.split_once("://") else { return Err(INVALID) };
        if authority.contains(['/', '?', '#'])
            || endpoint.port == 0
            || !endpoint.host.bytes().all(|b| b.is_ascii_alphanumeric() || b".-".contains(&b))
            || !(endpoint.tls && endpoint.port == 443 || endpoint.host == "127.0.0.1" && !endpoint.tls)
            || !identifier(&file.mailbox)
            || !digest(&file.token)
        {
            return Err(INVALID);
        }
        Ok(Self {
            endpoint,
            token: Secret::new(file.token.into_owned()),
            mailbox: file.mailbox.into_owned(),
            key: private_key(&file.private_key).ok_or(INVALID)?,
        })
    }

    fn request(
        &self,
        outside: &dyn Outside,
        verb: &str,
        path: &str,
        body: Option<&Value>,
        key: Option<&str>,
        timeout: Duration,
    ) -> Result<Response, ServerError> {
        outside.reach(&self.endpoint.host).map_err(|_| UNREACHABLE)?;
        let bearer = Zeroizing::new(format!("Bearer {}", self.token.expose()));
        let mut headers = vec![("Authorization", bearer.as_str()), ("User-Agent", "HodHod-Mac/0.2")];
        if body.is_some() {
            headers.push(("Content-Type", "application/json"));
        }
        if let Some(key) = key {
            headers.push(("Idempotency-Key", key));
        }
        let body = body.map(|value| Zeroizing::new(value.to_string()));
        let raw = Zeroizing::new(http::request(
            verb,
            path,
            &self.endpoint.host_header(),
            &headers,
            body.as_ref().map(|body| body.as_bytes()),
        ));
        self.endpoint.send(&raw, timeout).map_err(|_| UNREACHABLE)
    }

    fn check(&self, outside: &dyn Outside, email: &str, timeout: Duration) -> Result<(), ServerError> {
        let accounts: Mailboxes = json_response(self.request(outside, "GET", "/v2/mailboxes", None, None, timeout)?)?;
        if accounts.mailboxes.iter().any(|m| m.id == self.mailbox && m.address.as_deref() == Some(email)) {
            return Ok(());
        }
        Err(INVALID)
    }

    fn decrypt(&self, message: &Message, sealed: &[u8]) -> Result<Vec<u8>, ServerError> {
        if !sealed.starts_with(b"HHE1") || sealed.len() < 34 {
            return Err(CORRUPT);
        }
        let nonce = Nonce::try_assume_unique_for_key(&sealed[4..16]).map_err(|_| CORRUPT)?;
        let wrapped = usize::from(u16::from_be_bytes([sealed[16], sealed[17]]));
        if wrapped < 256 || sealed.len() < 18 + wrapped + 16 {
            return Err(CORRUPT);
        }
        let data_key = Zeroizing::new(
            self.key
                .decrypt_data(Algorithm::RSAEncryptionOAEPSHA256, &sealed[18..18 + wrapped])
                .map_err(|_| CORRUPT)?,
        );
        let key = LessSafeKey::new(UnboundKey::new(&AES_256_GCM, &data_key).map_err(|_| CORRUPT)?);
        let text = format!("hodhod-message-v1/{}/{}/{}", self.mailbox, message.key_version, message.id);
        let mut bytes = sealed[18 + wrapped..].to_vec();
        let size = key.open_in_place(nonce, Aad::from(text.as_bytes()), &mut bytes).map_err(|_| CORRUPT)?.len();
        bytes.truncate(size);
        Ok(bytes)
    }
}

fn json_response<T: serde::de::DeserializeOwned>(response: Response) -> Result<T, ServerError> {
    match response.status {
        200 | 202 => serde_json::from_slice(&response.body).map_err(|_| INVALID),
        401 | 403 => Err(ServerError::Unreachable(mm_accounts::login_hold::REFUSED)),
        404 => Err(INVALID),
        _ => Err(UNREACHABLE),
    }
}

#[derive(Deserialize)]
struct Mailboxes {
    mailboxes: Vec<MailboxRow>,
}

#[derive(Deserialize)]
struct MailboxRow {
    id: String,
    address: Option<String>,
}

#[derive(Deserialize)]
struct Folders {
    folders: Vec<HostedFolder>,
}

#[derive(Deserialize)]
struct HostedFolder {
    id: String,
    name: String,
    role: Option<String>,
}

impl HostedFolder {
    fn name(&self) -> &str {
        if self.role.as_deref() == Some("inbox") { "INBOX" } else { &self.name }
    }

    fn folder(&self) -> Folder {
        let mut folder = Folder::named(self.name());
        let role = self.role.as_deref();
        if let Some(role @ ("sent" | "drafts" | "archive" | "all" | "junk" | "trash")) = role {
            folder.attributes.insert(format!("\\{role}"));
        }
        folder
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Changes {
    changes: Vec<Change>,
    cursor: u64,
    has_more: bool,
}

#[derive(Deserialize)]
struct Change {
    kind: String,
    action: String,
    id: String,
    #[serde(flatten)]
    message: Option<MessageRow>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct MessageRow {
    uid: u32,
    folder_id: String,
    key_version: u32,
    received_at: i64,
    is_read: u8,
}

struct Message {
    id: String,
    folder: String,
    key_version: u32,
    received: i64,
    read: bool,
}

pub(crate) fn probe(secret: &Secret, outside: &dyn Outside, email: &str, timeout: Duration) -> Result<(), ServerError> {
    Profile::read(secret)?.check(outside, email, timeout)
}

pub(crate) struct Session {
    profile: Profile,
    outside: std::sync::Arc<dyn Outside>,
    timeout: Duration,
    folders: Vec<HostedFolder>,
    rows: BTreeMap<u32, Message>,
    locations: BTreeMap<String, u32>,
    local_headers: BTreeMap<u32, Vec<u8>>,
    selected: Option<String>,
    cursor: u64,
    readonly: bool,
}

impl Session {
    pub(crate) fn open(
        account: &Account,
        directory: &Directory,
        outside: std::sync::Arc<dyn Outside>,
        timeout: Duration,
    ) -> Result<Self, ServerError> {
        let profile = Profile::read(&directory.password(account).map_err(ServerError::Failed)?)?;
        profile.check(&*outside, &account.email, timeout)?;
        let path = format!("/v2/mailboxes/{}/folders", profile.mailbox);
        let folders: Folders = json_response(profile.request(&*outside, "GET", &path, None, None, timeout)?)?;
        let mut session = Self {
            profile,
            outside,
            timeout,
            folders: folders.folders,
            rows: BTreeMap::new(),
            locations: BTreeMap::new(),
            local_headers: BTreeMap::new(),
            selected: None,
            cursor: 0,
            readonly: false,
        };
        session.refresh()?;
        Ok(session)
    }

    fn refresh(&mut self) -> Result<(), ServerError> {
        loop {
            let page: Changes = json_response(self.request("GET", &format!("changes?after={}", self.cursor), None)?)?;
            if page.cursor < self.cursor || (page.has_more && page.cursor == self.cursor) {
                return Err(INVALID);
            }
            for change in page.changes {
                if change.kind != "message" || !digest(&change.id) {
                    continue;
                }
                let replacing = self.locations.remove(&change.id);
                if change.action == "delete" {
                    if let Some(old) = replacing {
                        self.rows.remove(&old);
                        self.local_headers.remove(&old);
                    }
                } else if let Some(row) = change.message {
                    if row.uid == 0 || !identifier(&row.folder_id) {
                        return Err(INVALID);
                    }
                    if let Some(old) = replacing.filter(|old| *old != row.uid) {
                        self.rows.remove(&old);
                        self.local_headers.remove(&old);
                    }
                    self.locations.insert(change.id.clone(), row.uid);
                    self.rows.insert(
                        row.uid,
                        Message {
                            id: change.id,
                            folder: row.folder_id,
                            key_version: row.key_version,
                            received: row.received_at / 1000,
                            read: row.is_read != 0,
                        },
                    );
                } else if change.action == "upsert" {
                    // A past upsert can predate a deletion whose ciphertext was already removed.
                    if let Some(old) = replacing {
                        self.rows.remove(&old);
                        self.local_headers.remove(&old);
                    }
                }
            }
            self.cursor = page.cursor;
            if !page.has_more {
                return Ok(());
            }
        }
    }

    fn request(&self, verb: &str, tail: &str, body: Option<&Value>) -> Result<Response, ServerError> {
        let path = format!("/v2/mailboxes/{}/{tail}", self.profile.mailbox);
        self.profile.request(&*self.outside, verb, &path, body, None, self.timeout)
    }

    fn folder(&self, name: &str) -> Result<&HostedFolder, ServerError> {
        self.folders
            .iter()
            .find(|folder| folder.name() == name || (name.eq_ignore_ascii_case("INBOX") && folder.name() == "INBOX"))
            .ok_or(ServerError::Refused(NO_SUCH_FOLDER))
    }

    fn opened(&self, uid: u32) -> Option<&Message> {
        self.rows.get(&uid).filter(|m| self.selected.as_deref() == Some(m.folder.as_str()))
    }

    fn flags_for(row: &Message) -> Vec<String> {
        if row.read { vec!["\\Seen".to_owned()] } else { Vec::new() }
    }

    fn change(&self, uid: u32, fields: &Value) -> Result<(), ServerError> {
        if self.readonly {
            return Err(UNSUPPORTED);
        }
        let row = self.opened(uid).ok_or(UNKNOWN_UID)?;
        let _: Value = json_response(self.request("PATCH", &format!("messages/{}", row.id), Some(fields))?)?;
        Ok(())
    }

    fn header(&mut self, uid: u32) -> Result<Option<Vec<u8>>, ServerError> {
        if let Some(bytes) = self.local_headers.get(&uid) {
            return Ok(Some(bytes.clone()));
        }
        let Some(mut raw) = self.body(uid)? else { return Ok(None) };
        let length = raw
            .windows(4)
            .position(|w| w == b"\r\n\r\n")
            .map(|n| n + 4)
            .or_else(|| raw.windows(2).position(|w| w == b"\n\n").map(|n| n + 2));
        if let Some(length) = length {
            raw.truncate(length);
        }
        self.local_headers.insert(uid, raw.clone());
        Ok(Some(raw))
    }
}

impl MailSession for Session {
    fn folders(&mut self) -> Result<Vec<Folder>, SessionError> {
        Ok(self.folders.iter().map(HostedFolder::folder).collect())
    }

    fn status(&mut self, name: &str) -> Result<(i64, i64), SessionError> {
        let folder = &self.folder(name)?.id;
        Ok(self
            .rows
            .values()
            .filter(|m| &m.folder == folder)
            .fold((0, 0), |(unseen, count), row| (unseen + i64::from(!row.read), count + 1)))
    }

    fn logout(&mut self) {}
}

impl MailServer for Session {
    fn capabilities(&self) -> Vec<String> {
        vec!["MOVE".to_owned()]
    }

    fn select(&mut self, name: &str, readonly: bool) -> Result<Selected, ServerError> {
        let folder = self.folder(name)?.id.clone();
        let uids: Vec<_> = self.rows.iter().filter(|(_, m)| m.folder == folder).map(|(uid, _)| *uid).collect();
        self.selected = Some(folder);
        self.readonly = readonly;
        Ok(Selected {
            exists: u32::try_from(uids.len()).unwrap_or(u32::MAX),
            uidvalidity: Some(1),
            uidnext: self.rows.keys().last().and_then(|uid| uid.checked_add(1)),
            highest_modseq: None,
        })
    }

    fn search(&mut self, criteria: Search<'_>) -> Result<Vec<u32>, ServerError> {
        let candidates: Vec<_> = self
            .rows
            .iter()
            .filter(|(_, m)| self.selected.as_deref() == Some(&m.folder))
            .map(|(uid, m)| (*uid, m.received))
            .collect();
        let mut found = Vec::new();
        for (uid, received) in candidates {
            let included = match criteria {
                Search::All | Search::Undeleted | Search::ChangedSince(_) => true,
                Search::Above(low) => uid > low,
                Search::Below(high) => uid < high,
                Search::Uid(wanted) => uid == wanted,
                Search::Since(day) => received.div_euclid(86400) >= day,
                Search::MessageId(wanted) | Search::UndeletedMessageId(wanted) => {
                    self.header(uid)?.is_some_and(|raw| {
                        let fields = mm_content::mime::fields(&raw);
                        fields.message_id.trim_matches(['<', '>']) == wanted.trim_matches(['<', '>'])
                    })
                }
                Search::From(wanted) => self.header(uid)?.is_some_and(|raw| {
                    mm_content::mime::fields(&raw).from_addr.to_ascii_lowercase().contains(&wanted.to_ascii_lowercase())
                }),
            };
            if included {
                found.push(uid);
            }
        }
        Ok(found)
    }

    fn flags(&mut self, uids: &[u32]) -> Result<BTreeMap<u32, Vec<String>>, ServerError> {
        Ok(uids.iter().filter_map(|uid| self.opened(*uid).map(|row| (*uid, Self::flags_for(row)))).collect())
    }

    fn enable_condstore(&mut self) -> Result<bool, ServerError> {
        Ok(false)
    }

    fn headers(&mut self, uids: &[u32], _fields: &[&str], _gmail: bool) -> Result<Vec<Headers>, ServerError> {
        let mut result = Vec::new();
        for uid in uids {
            if let Some(row) = self.opened(*uid) {
                let flags = Self::flags_for(row);
                if let Some(raw) = self.header(*uid)? {
                    result.push(Headers { uid: *uid, flags, raw, ..Headers::default() });
                }
            }
        }
        Ok(result)
    }

    fn body(&mut self, uid: u32) -> Result<Option<Vec<u8>>, ServerError> {
        let Some(row) = self.opened(uid) else { return Ok(None) };
        let response = self.request("GET", &format!("messages/{}", row.id), None)?;
        if response.status == 404 {
            return Ok(None);
        }
        if response.status == 401 || response.status == 403 {
            return Err(ServerError::Unreachable(mm_accounts::login_hold::REFUSED));
        }
        if response.status != 200 {
            return Err(UNREACHABLE);
        }
        self.profile.decrypt(row, &response.body).map(Some)
    }

    fn arrivals(&mut self, uids: &[u32]) -> Result<BTreeMap<u32, i64>, ServerError> {
        Ok(uids.iter().filter_map(|uid| self.opened(*uid).map(|m| (*uid, m.received))).collect())
    }

    fn store_flags(&mut self, uids: &[u32], add: bool, flags: &[&str]) -> Result<(), ServerError> {
        if flags.iter().any(|flag| !flag.eq_ignore_ascii_case("\\Seen")) {
            return Err(UNSUPPORTED);
        }
        for uid in uids {
            self.change(*uid, &json!({"isRead": add}))?;
            if let Some(row) = self.rows.get_mut(uid) {
                row.read = add;
            }
        }
        Ok(())
    }

    fn store_labels(&mut self, _: &[u32], _: bool, _: &[&str]) -> Result<(), ServerError> {
        Err(UNSUPPORTED)
    }
    fn copy(&mut self, _: &[u32], _: &str) -> Result<(), ServerError> {
        Err(UNSUPPORTED)
    }

    fn uid_move(&mut self, uids: &[u32], dest: &str) -> Result<(), ServerError> {
        let fields = json!({"folderId": self.folder(dest)?.id});
        for uid in uids {
            self.change(*uid, &fields)?;
        }
        self.refresh()
    }

    fn expunge(&mut self, _: &[u32]) -> Result<(), ServerError> {
        Err(UNSUPPORTED)
    }

    fn append(&mut self, _: &str, _: &[u8], _: &[&str], _: Option<&str>) -> Result<Option<u32>, ServerError> {
        Err(UNSUPPORTED)
    }

    fn shutdown(&mut self) {}

    fn noop(&mut self) -> Result<(), ServerError> {
        self.refresh()
    }

    fn set_timeout(&mut self, seconds: u64) {
        self.timeout = Duration::from_secs(seconds);
    }
}

#[derive(Deserialize)]
struct Submission {
    id: String,
    state: String,
}

pub(crate) fn send(
    account: &Account,
    directory: &Directory,
    outside: &dyn Outside,
    raw: &[u8],
    recipients: &[String],
) -> Result<(), NotSent> {
    let unavailable = NotSent { said: "hosted mailbox unavailable", permanent: false, unanswered: false };
    let rejected = NotSent { said: "hosted mailbox rejected the message", permanent: true, unanswered: false };
    let profile = Profile::read(&directory.password(account).map_err(|_| unavailable)?).map_err(|_| rejected)?;
    let mut key = Sha256::new();
    key.update(account.email.as_bytes());
    key.update([0]);
    key.update(raw);
    for recipient in recipients {
        key.update([0]);
        key.update(recipient.as_bytes());
    }
    let key: String = key.finalize().iter().map(|byte| format!("{byte:02x}")).collect();
    let payload = json!({"from": account.email, "recipients": recipients, "mime": STANDARD.encode(raw)});
    let path = format!("/v2/mailboxes/{}/submissions", profile.mailbox);
    let deadline = Instant::now() + Duration::from_secs(45);
    let response = profile
        .request(outside, "POST", &path, Some(&payload), Some(&key), Duration::from_secs(15))
        .map_err(|_| unavailable)?;
    if matches!(response.status, 400 | 403 | 404 | 409 | 413) {
        return Err(rejected);
    }
    let mut submission: Submission = json_response(response).map_err(|_| unavailable)?;
    if !digest(&submission.id) {
        return Err(rejected);
    }
    while submission.state == "queued" && Instant::now() < deadline {
        std::thread::sleep(Duration::from_secs(2));
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Err(unavailable);
        }
        submission = json_response(
            profile
                .request(outside, "GET", &format!("{path}/{}", submission.id), None, None, remaining)
                .map_err(|_| unavailable)?,
        )
        .map_err(|_| unavailable)?;
        if !digest(&submission.id) {
            return Err(rejected);
        }
    }
    match submission.state.as_str() {
        "accepted" => Ok(()),
        "failed" | "bounced" => Err(rejected),
        _ => Err(unavailable),
    }
}

#[cfg(test)]
mod tests {
    use super::ProfileFile;

    #[test]
    fn profile_reader_accepts_slashes_escaped_by_the_mac_encoder() {
        let profile: Option<ProfileFile<'_>> = serde_json::from_str(
            r#"{"origin":"https:\/\/mailer.example.test","token":"demo","mailbox":"mailbox","privateKey":"a\/b"}"#,
        )
        .ok();
        assert!(profile.is_some_and(|p| { p.origin == "https://mailer.example.test" && p.private_key == "a/b" }));
    }
}
