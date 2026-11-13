//! Which thread a request names (`mailmind.attention.application.threads`).

use std::sync::Arc;

use mm_kernel::errors::Fault;

use crate::ports::{Accounts, AttentionBook};

/// What a request names a thread by: its account and id, or a message of it, as the pk its key names now.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Named {
    pub account: Option<String>,
    pub thread_id: Option<String>,
    pub pk: Option<i64>,
}

#[derive(Debug)]
pub struct Threads {
    book: Arc<dyn AttentionBook>,
    accounts: Arc<dyn Accounts>,
}

fn given(text: Option<&String>) -> Option<&str> {
    text.map(String::as_str).filter(|t| !t.is_empty())
}

impl Threads {
    pub fn new(book: Arc<dyn AttentionBook>, accounts: Arc<dyn Accounts>) -> Threads {
        Threads { book, accounts }
    }

    /// (account, thread id) from the account and id named, or the message: None for an account the app doesn't use,
    /// a message that isn't there, or no thread.
    pub fn target(&self, named: &Named) -> Result<Option<(String, String)>, Fault> {
        let (mut account, mut thread_id) =
            (given(named.account.as_ref()).map(str::to_owned), given(named.thread_id.as_ref()).map(str::to_owned));
        if let Some(pk) = named.pk
            && !(account.is_some() && thread_id.is_some())
        {
            let Some(row) = self.book.message(pk)? else {
                return Ok(None);
            };
            // Never a pk standing in for a thread id: a mute keyed so would match no thread and outlive none.
            account = account.or(Some(row.account).filter(|a| !a.is_empty()));
            thread_id = thread_id.or(row.thread_id.filter(|t| !t.is_empty()));
        }
        let (Some(account), Some(thread_id)) = (account, thread_id) else {
            return Ok(None);
        };
        if !self.accounts.emails()?.contains(&account) {
            return Ok(None);
        }
        Ok(Some((account, thread_id)))
    }
}
