//! A mute: a thread silenced, here and on its server (`mailmind.attention.domain.mute`, `application.muting`). What
//! it marks read, Undo marks unread again, and no more than that.

use std::sync::Arc;

use mm_kernel::errors::{Failure, Problem};
use mm_kernel::seams::Clock;
use serde_json::{Value, json};

use crate::ports::{Accounts, AttentionBook, Copy, Flags, Undo};
use crate::threads::{Named, Threads};

/// What a mute came to.
#[derive(Clone, Debug, PartialEq)]
pub enum Muted {
    Done(Value),
    /// The thread is muted here, but marking its unread mail read is a change on its server, which this server
    /// can't make yet.
    MarkNotYet,
}

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

#[derive(Debug)]
pub struct Muting {
    book: Arc<dyn AttentionBook>,
    threads: Threads,
    undo: Arc<dyn Undo>,
    flags: Arc<dyn Flags>,
    clock: Arc<dyn Clock>,
}

impl Muting {
    pub fn new(
        book: Arc<dyn AttentionBook>,
        accounts: Arc<dyn Accounts>,
        undo: Arc<dyn Undo>,
        flags: Arc<dyn Flags>,
        clock: Arc<dyn Clock>,
    ) -> Muting {
        Muting { threads: Threads::new(book.clone(), accounts), book, undo, flags, clock }
    }

    /// `POST /api/mute`. A thread muted already is left as it is: by then its mail reads as all seen, and an Undo that
    /// remembered that would mark nothing unread.
    pub fn mute(&self, named: &Named, undo_group: Option<&str>) -> Result<Muted, Failure> {
        let Some((account, thread_id)) = self.threads.target(named)? else {
            return Err(Problem::refused("bad mute target").into());
        };
        let when = mm_kernel::when::iso(self.clock.now());
        let Some(found) = self.book.mute(&account, &thread_id, &when)? else {
            return Ok(Muted::Done(json!({"ok": true, "thread_id": thread_id})));
        };
        let marked = found.unread.is_empty() || self.flags.mark_seen(&found.unread, true)?.is_some();
        let unread: &[Copy] = if marked { &found.unread } else { &[] };
        self.undo.push(&undo_entry(&account, &thread_id, unread, found.member.as_ref()), undo_group)?;
        Ok(if marked { Muted::Done(json!({"ok": true, "thread_id": thread_id})) } else { Muted::MarkNotYet })
    }

    /// `POST /api/unmute`.
    pub fn unmute(&self, named: &Named) -> Result<Value, Failure> {
        let Some((account, thread_id)) = self.threads.target(named)? else {
            return Err(Problem::refused("bad mute target").into());
        };
        self.book.unmute(&account, &thread_id)?;
        Ok(json!({"ok": true, "thread_id": thread_id}))
    }
}
