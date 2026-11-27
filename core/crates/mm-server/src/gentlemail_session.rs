use crate::gentlemail::MailboxSession;
use mm_accounts::{Account, Directory};
use mm_kernel::seams::Outside;
use mm_mailbox::ports::{MailSession, SessionError};
use mm_mailbox::roles::Folder;
use mm_mailbox::session::{Headers, MailServer, NO_SUCH_FOLDER, Search, Selected, ServerError};
use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::Duration;
const UNSUPPORTED: ServerError = ServerError::Refused("GentleMailUnsupported");
struct Message {
    folder: String,
    id: String,
    received: i64,
    read: bool,
    header: Vec<u8>,
}
pub(crate) struct Session {
    client: MailboxSession,
    outside: Arc<dyn Outside>,
    timeout: Duration,
    rows: BTreeMap<u32, Message>,
    selected: Option<String>,
    folders: Vec<HostedFolder>,
}
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

impl Session {
    pub fn open(
        account: &Account,
        directory: &Directory,
        outside: Arc<dyn Outside>,
        timeout: Duration,
    ) -> Result<Self, ServerError> {
        let key = directory.password(account).map_err(ServerError::Failed)?;
        let client = MailboxSession::open(account, key, &*outside, timeout)?;
        let folders = ["inbox", "sent", "archive", "spam", "trash"]
            .into_iter()
            .map(|id| HostedFolder {
                id: id.into(),
                name: id.into(),
                role: Some(if id == "spam" { "junk" } else { id }.into()),
            })
            .collect();
        let mut session = Self { client, outside, timeout, rows: BTreeMap::new(), selected: None, folders };
        session.refresh()?;
        Ok(session)
    }
    fn refresh(&mut self) -> Result<(), ServerError> {
        let mut rows = BTreeMap::new();
        for (uid, m) in self.client.snapshot(&*self.outside, self.timeout)? {
            rows.insert(
                uid,
                Message { received: m.timestamp()?, header: m.headers()?, id: m.id, folder: m.folder, read: m.is_read },
            );
        }
        self.rows = rows;
        Ok(())
    }
    fn header(&self, uid: u32) -> Result<Option<Vec<u8>>, ServerError> {
        Ok(self.opened(uid).map(|m| m.header.clone()))
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
        Vec::new()
    }

    fn select(&mut self, name: &str, _readonly: bool) -> Result<Selected, ServerError> {
        let folder = self.folder(name)?.id.clone();
        let uids: Vec<_> = self.rows.iter().filter(|(_, m)| m.folder == folder).map(|(uid, _)| *uid).collect();
        self.selected = Some(folder);
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
        self.client.body(&*self.outside, &row.id, self.timeout).map(Some)
    }
    fn arrivals(&mut self, uids: &[u32]) -> Result<BTreeMap<u32, i64>, ServerError> {
        Ok(uids.iter().filter_map(|uid| self.opened(*uid).map(|m| (*uid, m.received))).collect())
    }

    fn store_flags(&mut self, _: &[u32], _: bool, _: &[&str]) -> Result<(), ServerError> {
        Err(UNSUPPORTED)
    }
    fn store_labels(&mut self, _: &[u32], _: bool, _: &[&str]) -> Result<(), ServerError> {
        Err(UNSUPPORTED)
    }
    fn copy(&mut self, _: &[u32], _: &str) -> Result<(), ServerError> {
        Err(UNSUPPORTED)
    }

    fn uid_move(&mut self, _: &[u32], _: &str) -> Result<(), ServerError> {
        Err(UNSUPPORTED)
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
