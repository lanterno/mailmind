//! The change queue's ports bound to their adapters (`bootstrap.mailbox`'s commands): the accounts a change needs,
//! from the Directory, and names no one can guess, from the operating system's random source.

use std::fmt::Write;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};

use mm_accounts::Directory;
use mm_kernel::errors::Fault;
use mm_mailbox::command_ports::{MoveAccount, MoveAccounts, Tokens};
use mm_mailbox::destination::Provider;

/// What a change to an account's mail needs of it: its Trash folder and its provider's ways.
#[derive(Debug)]
pub(crate) struct DirectoryMoveAccounts(pub(crate) Arc<Directory>);

impl MoveAccounts for DirectoryMoveAccounts {
    fn move_account(&self, email: &str) -> Result<Option<MoveAccount>, Fault> {
        Ok(self.0.get(email)?.map(|a| MoveAccount {
            provider: Provider::of_kind(&a.kind),
            trash: a.trash,
            email: a.email,
        }))
    }

    fn emails(&self) -> Result<Vec<String>, Fault> {
        Ok(self.0.accounts()?.into_iter().map(|a| a.email).collect())
    }
}

/// `secrets.token_hex`: bytes from the operating system's random source. Should that ever fail, a count this
/// process keeps still makes each name its own.
#[derive(Debug, Default)]
pub(crate) struct RandomTokens {
    drawn: AtomicU64,
}

impl Tokens for RandomTokens {
    fn hex(&self, bytes: usize) -> String {
        let mut drawn = vec![0u8; bytes];
        if getrandom::fill(&mut drawn).is_err() {
            let count = self.drawn.fetch_add(1, Ordering::Relaxed).to_be_bytes();
            for (i, byte) in drawn.iter_mut().rev().enumerate() {
                *byte = count.get(count.len().wrapping_sub(i + 1)).copied().unwrap_or(0);
            }
        }
        drawn.iter().fold(String::with_capacity(bytes * 2), |mut out, b| {
            let _ = write!(out, "{b:02x}");
            out
        })
    }
}
