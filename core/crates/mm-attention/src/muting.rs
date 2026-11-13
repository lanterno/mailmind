//! A mute: a thread silenced, here and on its server (`mailmind.attention.domain.mute`, `application.muting`). What
//! it marks read, Undo marks unread again, and no more than that.

use std::sync::Arc;

use mm_kernel::errors::{Failure, Problem};
use mm_kernel::seams::Clock;
use serde_json::{Value, json};

use crate::events::AttentionEvent;
use crate::ports::{Accounts, AttentionBook, Copy, Events, Flags, Undo};
use crate::snoozing::copy_named;
use crate::threads::{Named, Threads};

fn copy_json(c: &Copy) -> Value {
    json!([c.key, c.folder])
}

/// The undo entry of a mute: what it marked read, by key and folder, since a pk isn't stable, and one member, which
/// can say later which thread this has become.
pub fn undo_entry(account: &str, thread_id: &str, marked: &[Copy], member: Option<&Copy>) -> Value {
    json!({
        "kind": "mute", "account": account, "thread_id": thread_id,
        "unread": marked.iter().map(copy_json).collect::<Vec<_>>(), "label": "Muted",
        "member": member.map(copy_json),
    })
}

/// What wires Muting.
#[derive(Debug)]
pub struct MutingParts {
    pub book: Arc<dyn AttentionBook>,
    pub accounts: Arc<dyn Accounts>,
    pub undo: Arc<dyn Undo>,
    pub flags: Arc<dyn Flags>,
    pub events: Arc<dyn Events>,
    pub clock: Arc<dyn Clock>,
}

#[derive(Debug)]
pub struct Muting {
    book: Arc<dyn AttentionBook>,
    threads: Threads,
    undo: Arc<dyn Undo>,
    flags: Arc<dyn Flags>,
    events: Arc<dyn Events>,
    clock: Arc<dyn Clock>,
}

impl Muting {
    pub fn new(parts: MutingParts) -> Muting {
        let MutingParts { book, accounts, undo, flags, events, clock } = parts;
        Muting { threads: Threads::new(book.clone(), accounts), book, undo, flags, events, clock }
    }

    /// `POST /api/mute`: the thread silenced, and its unread mail marked read, here and on its server. A thread muted
    /// already is left as it is: by then its mail reads as all seen, and an Undo that remembered that would mark
    /// nothing unread.
    pub fn mute(&self, named: &Named, undo_group: Option<&str>) -> Result<Value, Failure> {
        let Some((account, thread_id)) = self.threads.target(named)? else {
            return Err(Problem::refused("bad mute target").into());
        };
        let when = mm_kernel::when::iso(self.clock.now());
        let Some(found) = self.book.mute(&account, &thread_id, &when)? else {
            return Ok(json!({"ok": true, "thread_id": thread_id}));
        };
        self.undo.push(&undo_entry(&account, &thread_id, &found.unread, found.member.as_ref()), undo_group)?;
        self.events.publish(AttentionEvent::ThreadMuted { account, thread_id: thread_id.clone() });
        if !found.unread.is_empty() {
            self.flags.flags_queued();
        }
        Ok(json!({"ok": true, "thread_id": thread_id}))
    }

    /// `POST /api/unmute`.
    pub fn unmute(&self, named: &Named) -> Result<Value, Failure> {
        let Some((account, thread_id)) = self.threads.target(named)? else {
            return Err(Problem::refused("bad mute target").into());
        };
        self.book.unmute(&account, &thread_id)?;
        self.events.publish(AttentionEvent::ThreadUnmuted { account, thread_id: thread_id.clone() });
        Ok(json!({"ok": true, "thread_id": thread_id}))
    }

    /// Undo of a mute: unmuted, the thread the entry names and the one its member is in now, and unread again what
    /// it marked read, here and on its server. (An entry from before muting told the server, which kept each member's
    /// `prev_seen`, is a day past what the stack keeps.)
    pub fn reverse(&self, entry: &Value) -> Result<(), Failure> {
        let text = |name: &str| entry.get(name).and_then(Value::as_str).unwrap_or_default();
        let member = entry.get("member").and_then(copy_named);
        let marked: Vec<Copy> = entry
            .get("unread")
            .and_then(Value::as_array)
            .map(|l| l.iter().filter_map(copy_named).collect())
            .unwrap_or_default();
        let when = mm_kernel::when::iso(self.clock.now());
        if self.book.unmute_reversed(text("account"), text("thread_id"), member.as_ref(), &marked, &when)? {
            self.flags.flags_queued();
        }
        Ok(())
    }
}
