//! A draft as it opens in the reader (`compose.drafts.info`): its `draft_id` and mode, so the page can open it back
//! into the composer. A draft no row tracks yet, another app's found on its server, is tracked as it opens. Another
//! message names the reply already saved to it, which Reply opens.

use std::collections::HashSet;
use std::fmt;
use std::sync::Arc;

use mm_kernel::errors::Fault;
use mm_kernel::seams::Clock;
use mm_kernel::when::iso;
use serde_json::{Value, json};

use crate::draft::{self, Answer};
use crate::ports::{DraftFolders, Row, StoredDrafts, Tracked};

fn col<'a>(r: &'a Row, name: &str) -> &'a Value {
    r.get(name).unwrap_or(&Value::Null)
}

fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

pub struct Drafts {
    stored: Arc<dyn StoredDrafts>,
    folders: Arc<dyn DraftFolders>,
    clock: Arc<dyn Clock>,
}

impl fmt::Debug for Drafts {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Drafts").finish_non_exhaustive()
    }
}

impl Drafts {
    pub fn new(stored: Arc<dyn StoredDrafts>, folders: Arc<dyn DraftFolders>, clock: Arc<dyn Clock>) -> Drafts {
        Drafts { stored, folders, clock }
    }

    /// Whether a message is a draft (`draft.is_draft`): its server flags it \Draft, or it sits in the account's
    /// Drafts, never by what a folder is called.
    pub fn is_draft(&self, row: &Row) -> Result<bool, Fault> {
        if truthy(col(row, "draft")) {
            return Ok(true);
        }
        let (account, folder) = (col(row, "account").as_str(), col(row, "folder").as_str());
        self.folders.plays_drafts(account.unwrap_or_default(), folder.unwrap_or_default())
    }

    /// For a draft, its `draft_id`, `draft_mode` and `is_draft`. For another message, the reply already saved to it, if
    /// there is one (`reply_draft`), so Reply opens that rather than a second, empty one.
    pub fn info(&self, row: &Row) -> Result<Row, Fault> {
        if !self.is_draft(row)? {
            let mut info = Row::new();
            if let Some(waiting) = self.answering(row)? {
                info.insert("reply_draft".to_owned(), waiting);
            }
            return Ok(info);
        }
        let account = col(row, "account").as_str().unwrap_or_default();
        let message_id = col(row, "message_id").as_str();
        let (id, mode) = match self.stored.by_message_id(account, message_id)? {
            Some(d) => {
                let mode = col(&d, "mode");
                (col(&d, "id").clone(), if truthy(mode) { mode.clone() } else { json!("new") })
            }
            None => {
                let folder = col(row, "folder").as_str();
                let tracked = Tracked {
                    account: account.to_owned(),
                    folder: folder.map(str::to_owned),
                    uid: col(row, "uid").as_i64(),
                    message_id: message_id.map(str::to_owned),
                    uidvalidity: self.stored.uidvalidity(account, folder.unwrap_or_default())?,
                    mode: Some("new".to_owned()),
                    subject: col(row, "subject").as_str().map(str::to_owned),
                    at: iso(self.clock.now()),
                };
                (json!(self.stored.track(&tracked)?), json!("new"))
            }
        };
        let mut info = Row::new();
        info.insert("draft_id".to_owned(), id);
        info.insert("draft_mode".to_owned(), mode);
        info.insert("is_draft".to_owned(), json!(true));
        Ok(info)
    }

    /// The newest draft that answers this message, and how many more do (`answering`): MailMind's own by what its last
    /// save answered, wherever that save is now, and another app's by its copy in Drafts, whose In-Reply-To names the
    /// message. None a send holds or a discard is taking. Reads only what is stored here.
    pub fn answering(&self, row: &Row) -> Result<Option<Value>, Fault> {
        let Some(message_id) = col(row, "message_id").as_str().filter(|m| !m.is_empty()) else { return Ok(None) };
        let own = self.stored.answering(message_id)?;
        let mut ids: HashSet<i64> = own.iter().filter_map(|d| col(d, "id").as_i64()).collect();
        let mut found: Vec<Answer> = own
            .iter()
            .map(|d| Answer {
                saved_at: col(d, "saved_at").as_i64().unwrap_or(0),
                draft_id: col(d, "id").as_i64(),
                in_drafts: None,
            })
            .collect();
        for m in self.stored.naming(message_id)? {
            if draft::answers(col(&m, "in_reply_to").as_str()).as_deref() != Some(message_id) || !self.is_draft(&m)? {
                continue;
            }
            let account = col(&m, "account").as_str().unwrap_or_default();
            let tracked = self.stored.by_message_id(account, col(&m, "message_id").as_str())?;
            let tracked_id = tracked.as_ref().and_then(|d| col(d, "id").as_i64());
            if let Some(id) = tracked_id
                && (ids.contains(&id) || self.stored.held(id)?)
            {
                continue;
            }
            let saved_at = col(&m, "ts").as_i64().unwrap_or(0);
            match tracked_id {
                // MailMind tracks it, from before its rows said what it answers.
                Some(id) => {
                    ids.insert(id);
                    found.push(Answer { saved_at, draft_id: Some(id), in_drafts: None });
                }
                None => found.push(Answer {
                    saved_at,
                    draft_id: None,
                    in_drafts: Some((
                        col(&m, "message_key").as_i64().unwrap_or(0),
                        col(&m, "folder").as_str().unwrap_or_default().to_owned(),
                        col(&m, "uid").as_i64().unwrap_or(0),
                    )),
                }),
            }
        }
        Ok(draft::newest_answer(&found))
    }
}
