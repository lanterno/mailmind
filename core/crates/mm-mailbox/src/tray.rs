//! The tray of changes (`MailCommands.list_open`, `domain.command.tray_item`): the changes a person made that
//! haven't reached a server, and how many wait and how many were given up on.

use std::sync::Arc;

use mm_kernel::errors::{Failure, Fault};
use serde_json::{Map, Value, json};

use crate::listing::{col, text, truthy};
use crate::ports::StoredCommands;

const UNREADABLE: Fault = Fault::new("JSONDecodeError");

#[derive(Debug)]
pub struct Tray {
    stored: Arc<dyn StoredCommands>,
}

impl Tray {
    pub fn new(stored: Arc<dyn StoredCommands>) -> Tray {
        Tray { stored }
    }

    /// What the tray shows. A subject the change doesn't hold comes from its message, "" when that has gone.
    pub fn list_open(&self, limit: i64) -> Result<Value, Failure> {
        let (rows, counts) = self.stored.open_commands(limit)?;
        let mut items = Vec::with_capacity(rows.len());
        for r in &rows {
            let params: Map<String, Value> = match text(r, "params").filter(|p| !p.is_empty()) {
                None => Map::new(),
                Some(p) => serde_json::from_str(p).map_err(|_| UNREADABLE)?,
            };
            let subject = match params.get("subject").filter(|s| !s.is_null()) {
                Some(subject) => subject.clone(),
                None => {
                    let pk = params.get("pk").filter(|p| truthy(p)).and_then(Value::as_i64);
                    let message_id = text(r, "message_id").filter(|m| !m.is_empty());
                    self.stored
                        .command_subject(
                            text(r, "account").unwrap_or_default(),
                            text(r, "src_folder").unwrap_or_default(),
                            message_id,
                            pk,
                        )?
                        .unwrap_or_else(|| json!(""))
                }
            };
            items.push(json!({
                "id": col(r, "id"), "account": col(r, "account"), "op": col(r, "kind"), "folder": col(r, "src_folder"),
                "dest": col(r, "dest"), "flags": params.get("flags").cloned().unwrap_or(Value::Null),
                "state": col(r, "state"), "attempts": col(r, "attempts"), "error": col(r, "last_error"),
                "subject": subject, "next_attempt_at": col(r, "next_attempt_at"), "created_at": col(r, "created_at"),
            }));
        }
        let count = |state: &str| counts.iter().find(|(s, _)| s == state).map_or(0, |(_, n)| *n);
        Ok(json!({"items": items, "waiting": count("queued") + count("running"), "stuck": count("error")}))
    }
}
