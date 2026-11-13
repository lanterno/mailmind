//! What the page lists (`mailmind.mailbox.application.queries`): a folder, every account's Inbox as one, a
//! conversation, and the views that aren't folders (Snoozed, Muted, Waiting, Reply Later). Each read is of the
//! database alone, but that a folder opening may start its sync.

use std::sync::Arc;

use mm_kernel::errors::{Failure, Problem};
use mm_kernel::seams::Clock;
use serde_json::{Map, Value, json};

use crate::listing;
use crate::ports::{AccountBook, FolderSyncs, Opened, Row, StoredLists, StoredMail};

pub const INBOX: &str = "INBOX";

/// The views that aren't folders, by the name the page gives each as its folder.
const SNOOZED: &str = "__snoozed__";
const MUTED: &str = "__muted__";
const WAITING: &str = "__waiting__";
const REPLY_LATER: &str = "__replylater__";

#[derive(Debug)]
pub struct Lists {
    accounts: Arc<dyn AccountBook>,
    stored: Arc<dyn StoredLists>,
    counts: Arc<dyn StoredMail>,
    syncs: Arc<dyn FolderSyncs>,
    clock: Arc<dyn Clock>,
}

/// Which page of a list, and whether as one row a conversation.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Paging {
    pub offset: i64,
    pub limit: i64,
    pub grouped: bool,
}

impl Lists {
    pub fn new(
        accounts: Arc<dyn AccountBook>,
        stored: Arc<dyn StoredLists>,
        counts: Arc<dyn StoredMail>,
        syncs: Arc<dyn FolderSyncs>,
        clock: Arc<dyn Clock>,
    ) -> Lists {
        Lists { accounts, stored, counts, syncs, clock }
    }

    /// What a view that isn't a folder reads: the account named, or with none named every account, as the
    /// Inboxes' list does.
    fn over(&self, account: Option<&str>) -> Result<Vec<String>, Failure> {
        Ok(match account {
            Some(account) => vec![account.to_owned()],
            None => self.accounts.emails()?,
        })
    }

    /// A list by folder. A view that isn't a folder reads every account when none is named; a real folder is one
    /// account's.
    pub fn messages_in(&self, account: Option<&str>, folder: &str, page: Paging) -> Result<Value, Failure> {
        match folder {
            SNOOZED => return self.snoozed_in(account, page),
            MUTED => return self.view_in(account, page, MUTED, "muted", listing::muted_item),
            WAITING => return self.view_in(account, page, WAITING, "waiting", listing::waiting_item),
            REPLY_LATER => return self.view_in(account, page, REPLY_LATER, "replylater", listing::replylater_item),
            _ => {}
        }
        let Some(account) = account else {
            return Ok(json!({"ok": true, "account": null, "folder": folder, "items": [], "grouped": page.grouped,
                             "total": 0, "offset": page.offset, "has_more": false}));
        };
        let scan = if page.offset == 0 { self.syncs.on_open(account, folder)? } else { Opened::Done };
        let found = self.stored.folder_page(account, folder, page.grouped, page.limit, page.offset)?;
        let now = self.clock.now();
        let items: Vec<Value> = found.rows.iter().map(|r| listing::listed_item(r, page.grouped, now)).collect();
        let mut answer = Map::new();
        answer.insert("ok".into(), json!(true));
        answer.insert("account".into(), json!(account));
        answer.insert("folder".into(), json!(folder));
        answer.insert("grouped".into(), json!(page.grouped));
        windowed(&mut answer, items, found.total, page.offset);
        match scan {
            Opened::Done => {}
            Opened::Syncing => _ = answer.insert("syncing".into(), json!(true)),
            Opened::SyncFailed => _ = answer.insert("sync_failed".into(), json!(true)),
            Opened::CatchingUp => _ = answer.insert("catching_up".into(), json!(true)),
        }
        Ok(Value::Object(answer))
    }

    /// Every account's Inbox as one list, newest first, each row naming its account. It answers from what is
    /// stored, so an account that's away or still on its first scan holds up no one.
    pub fn all_inboxes(&self, page: Paging) -> Result<Value, Failure> {
        let emails = self.accounts.emails()?;
        let mut scanning = false;
        if page.offset == 0 {
            for email in &emails {
                scanning |= self.syncs.scanning_first(email, INBOX)?;
            }
        }
        let found = self.stored.inboxes_page(&emails, page.grouped, page.limit, page.offset)?;
        let now = self.clock.now();
        let items: Vec<Value> = found.rows.iter().map(|r| listing::listed_item(r, page.grouped, now)).collect();
        let mut answer = Map::new();
        answer.insert("ok".into(), json!(true));
        answer.insert("folder".into(), json!(INBOX));
        answer.insert("grouped".into(), json!(page.grouped));
        windowed(&mut answer, items, found.total, page.offset);
        if scanning {
            answer.insert("syncing".into(), json!(true));
        }
        Ok(Value::Object(answer))
    }

    /// Every snoozed message in the account, or in every account, soonest wake first.
    fn snoozed_in(&self, account: Option<&str>, page: Paging) -> Result<Value, Failure> {
        let found = self.stored.snoozed_page(&self.over(account)?, page.limit, page.offset)?;
        let now = self.clock.now();
        let items: Vec<Value> = found.rows.iter().map(|r| listing::snoozed_item(r, now)).collect();
        let mut answer = Map::new();
        answer.insert("ok".into(), json!(true));
        answer.insert("account".into(), json!(account));
        answer.insert("folder".into(), json!(SNOOZED));
        windowed(&mut answer, items, found.total, page.offset);
        answer.insert("snoozed".into(), json!(true));
        Ok(Value::Object(answer))
    }

    /// Muted, Waiting or Reply Later: one row a thread, read whole and then cut to the page.
    fn view_in(
        &self,
        account: Option<&str>,
        page: Paging,
        folder: &str,
        marked: &str,
        item: fn(&Row, mm_kernel::seams::Moment) -> Value,
    ) -> Result<Value, Failure> {
        let accounts = self.over(account)?;
        let all = match folder {
            MUTED => self.stored.muted(&accounts)?,
            WAITING => self.stored.waiting(&accounts)?,
            _ => self.stored.reply_later(&accounts)?,
        };
        let total = all.len() as i64;
        let now = self.clock.now();
        let items: Vec<Value> =
            py_slice(&all, page.offset, page.offset.saturating_add(page.limit)).iter().map(|r| item(r, now)).collect();
        let mut answer = Map::new();
        answer.insert("ok".into(), json!(true));
        answer.insert("account".into(), json!(account));
        answer.insert("folder".into(), json!(folder));
        windowed(&mut answer, items, total, page.offset);
        answer.insert(marked.into(), json!(true));
        Ok(Value::Object(answer))
    }

    /// All messages in a conversation within an account, oldest to newest, each Message-ID once (so a Gmail copy
    /// in the Inbox and in All Mail shows once). Refused when either isn't named.
    pub fn thread(&self, account: Option<&str>, thread_id: Option<&str>) -> Result<Value, Failure> {
        let (Some(account), Some(thread_id)) = (account, thread_id) else {
            return Err(Problem::refused("missing account/thread").into());
        };
        let rows = self.stored.thread(account, thread_id)?;
        let items = listing::thread_items(&rows, self.clock.now());
        let reply_key = items.last().and_then(|it| it.get("key")).cloned().unwrap_or(Value::Null);
        let count = items.len();
        Ok(json!({"ok": true, "account": account, "thread_id": thread_id, "items": items, "count": count,
                  "reply_key": reply_key}))
    }

    /// The views that aren't folders, counted over every account, for the rail on All inboxes.
    pub fn across_accounts(&self) -> Result<Value, Failure> {
        let counts = self.counts.view_counts(&self.accounts.emails()?)?;
        Ok(json!({"ok": true, "snoozed": counts.snoozed, "muted": counts.muted, "waiting": counts.waiting,
                  "replylater": counts.replylater}))
    }
}

/// `items`, the total and where they stand in it.
fn windowed(answer: &mut Map<String, Value>, items: Vec<Value>, total: i64, offset: i64) {
    let shown = items.len();
    answer.insert("items".into(), Value::Array(items));
    for (name, value) in listing::window(shown, total, offset) {
        answer.insert(name.into(), value);
    }
}

/// `rows[start:stop]`, as Python slices a list: a negative end counts from the back, and either is held to it.
pub fn py_slice<T>(rows: &[T], start: i64, stop: i64) -> &[T] {
    let n = rows.len() as i64;
    let at = |i: i64| if i < 0 { (i + n).max(0) } else { i.min(n) } as usize;
    let (start, stop) = (at(start), at(stop));
    if start >= stop { &[] } else { &rows[start..stop] }
}
