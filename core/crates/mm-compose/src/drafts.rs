//! A draft as it opens in the reader (`compose.drafts.info`): its `draft_id` and mode, so the page can open it back
//! into the composer. A draft no row tracks yet, another app's found on its server, is tracked as it opens.

use std::fmt;
use std::sync::Arc;

use mm_kernel::errors::Fault;
use mm_kernel::seams::Clock;
use mm_kernel::when::iso;
use serde_json::{Value, json};

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

    /// For a draft, its `draft_id`, `draft_mode` and `is_draft`; nothing for another message.
    pub fn info(&self, row: &Row) -> Result<Row, Fault> {
        if !self.is_draft(row)? {
            return Ok(Row::new());
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
}
