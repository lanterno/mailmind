//! A sweep's mail back from Trash at once (`ServerOps::move_back_many`, as `ServerOps.move_back_many` does it in the
//! Python app), against a server that writes down what it was asked: one session for an account's hundreds, each
//! found by its exact Message-ID, one MOVE for them all, a refused one alone, and one still in the INBOX back already.

#![allow(clippy::unwrap_used)]

use std::collections::{BTreeMap, BTreeSet};
use std::sync::{Arc, Mutex};

use mm_mailbox::ports::{MailSession, SessionError};
use mm_mailbox::roles::Folder;
use mm_mailbox::server_ops::ServerOps;
use mm_mailbox::session::{Headers, MailServer, MailServers, Search, Selected, ServerError};

const ADA: &str = "ada@example.test";

/// The account's folders, each message by UID with its Message-ID; what was asked of them; and how it goes wrong.
#[derive(Debug, Default)]
struct Mail {
    folders: BTreeMap<String, BTreeMap<u32, String>>,
    next: u32,
    /// Message-IDs the server won't move.
    refused: BTreeSet<String>,
    /// The connection goes at the first MOVE.
    lost: bool,
    asked: Vec<String>,
}

#[derive(Debug, Clone, Default)]
struct Shared(Arc<Mutex<Mail>>);

impl Shared {
    fn with(folders: &[(&str, &[&str])]) -> Shared {
        let mut mail = Mail { next: 1, ..Mail::default() };
        for (folder, mids) in folders {
            let held = mail.folders.entry((*folder).to_owned()).or_default();
            for mid in *mids {
                held.insert(mail.next, (*mid).to_owned());
                mail.next += 1;
            }
        }
        Shared(Arc::new(Mutex::new(mail)))
    }

    fn mail(&self) -> std::sync::MutexGuard<'_, Mail> {
        self.0.lock().unwrap()
    }

    fn holds(&self, folder: &str) -> Vec<String> {
        self.mail().folders.get(folder).map(|f| f.values().cloned().collect()).unwrap_or_default()
    }
}

struct Server {
    mail: Shared,
    selected: String,
}

impl Server {
    fn moving(&mut self, uids: &[u32], dest: &str, verb: &str) -> Result<(), ServerError> {
        let mut mail = self.mail.mail();
        mail.asked.push(format!("{verb} {uids:?} {dest}"));
        if mail.lost {
            return Err(ServerError::Unreachable("abort"));
        }
        let held = mail.folders.get(&self.selected).cloned().unwrap_or_default();
        if uids.iter().any(|u| held.get(u).is_some_and(|mid| mail.refused.contains(mid))) {
            return Err(ServerError::Refused("NO"));
        }
        for uid in uids {
            let mid = mail.folders.get_mut(&self.selected).unwrap().remove(uid).unwrap();
            let next = mail.next;
            mail.folders.entry(dest.to_owned()).or_default().insert(next, mid);
            mail.next += 1;
        }
        Ok(())
    }
}

impl MailSession for Server {
    fn folders(&mut self) -> Result<Vec<Folder>, SessionError> {
        Ok(Vec::new())
    }

    fn status(&mut self, _folder: &str) -> Result<(i64, i64), SessionError> {
        Ok((0, 0))
    }

    fn logout(&mut self) {
        self.mail.mail().asked.push("LOGOUT".into());
    }
}

impl MailServer for Server {
    fn capabilities(&self) -> Vec<String> {
        vec!["MOVE".into()]
    }

    fn select(&mut self, folder: &str, readonly: bool) -> Result<Selected, ServerError> {
        self.mail.mail().asked.push(format!("{} {folder}", if readonly { "EXAMINE" } else { "SELECT" }));
        self.selected = folder.to_owned();
        Ok(Selected::default())
    }

    fn search(&mut self, criteria: Search<'_>) -> Result<Vec<u32>, ServerError> {
        let Search::MessageId(wanted) = criteria else { unreachable!("found by Message-ID") };
        let mail = self.mail.mail();
        let held = mail.folders.get(&self.selected).cloned().unwrap_or_default();
        Ok(held.into_iter().filter(|(_, mid)| mid == wanted).map(|(uid, _)| uid).collect())
    }

    fn flags(&mut self, _: &[u32]) -> Result<BTreeMap<u32, Vec<String>>, ServerError> {
        Ok(BTreeMap::new())
    }

    fn enable_condstore(&mut self) -> Result<bool, ServerError> {
        Ok(false)
    }

    fn headers(&mut self, _: &[u32], _: &[&str], _: bool) -> Result<Vec<Headers>, ServerError> {
        Ok(Vec::new())
    }

    fn body(&mut self, _: u32) -> Result<Option<Vec<u8>>, ServerError> {
        Ok(None)
    }

    fn arrivals(&mut self, _: &[u32]) -> Result<BTreeMap<u32, i64>, ServerError> {
        Ok(BTreeMap::new())
    }

    fn store_flags(&mut self, _: &[u32], _: bool, _: &[&str]) -> Result<(), ServerError> {
        Ok(())
    }

    fn store_labels(&mut self, _: &[u32], _: bool, _: &[&str]) -> Result<(), ServerError> {
        Err(ServerError::Refused("NO"))
    }

    fn copy(&mut self, uids: &[u32], dest: &str) -> Result<(), ServerError> {
        self.moving(uids, dest, "COPY")
    }

    fn uid_move(&mut self, uids: &[u32], dest: &str) -> Result<(), ServerError> {
        self.moving(uids, dest, "MOVE")
    }

    fn expunge(&mut self, _: &[u32]) -> Result<(), ServerError> {
        Ok(())
    }

    fn append(&mut self, _: &str, _: &[u8], _: &[&str], _: Option<&str>) -> Result<Option<u32>, ServerError> {
        Err(ServerError::Refused("NO"))
    }

    fn shutdown(&mut self) {
        self.mail.mail().asked.push("HANG UP".into());
    }
}

impl MailServers for Shared {
    fn open(&self, account: &str, _timeout_seconds: u64) -> Result<Box<dyn MailServer>, ServerError> {
        self.mail().asked.push(format!("LOGIN {account}"));
        Ok(Box::new(Server { mail: self.clone(), selected: String::new() }))
    }
}

fn ops(mail: &Shared) -> ServerOps {
    ServerOps::new(Arc::new(mail.clone()), false)
}

fn mids(names: &[&str]) -> Vec<String> {
    names.iter().map(|m| (*m).to_owned()).collect()
}

#[test]
fn an_accounts_mail_goes_back_on_one_session_in_one_move_and_one_still_in_the_inbox_is_back() {
    let mail = Shared::with(&[("INBOX", &["<kept@x>", "<never-left@x>"]), ("Trash", &["<a@x>", "<old@x>", "<b@x>"])]);
    let back =
        ops(&mail).move_back_many(ADA, "Trash", "INBOX", &mids(&["<a@x>", "<b@x>", "<never-left@x>", "<gone@x>"]));
    assert_eq!(back, Ok(vec![true, true, true, false]));
    assert_eq!(mail.holds("Trash"), ["<old@x>"]);
    assert_eq!(mail.holds("INBOX"), ["<kept@x>", "<never-left@x>", "<a@x>", "<b@x>"]);
    assert_eq!(
        mail.mail().asked,
        ["LOGIN ada@example.test", "SELECT Trash", "MOVE [3, 5] INBOX", "EXAMINE INBOX", "LOGOUT"]
    );
}

#[test]
fn a_message_the_server_wont_move_holds_back_no_other() {
    let mail = Shared::with(&[("Trash", &["<a@x>", "<stuck@x>", "<b@x>"])]);
    mail.mail().refused.insert("<stuck@x>".into());
    let back = ops(&mail).move_back_many(ADA, "Trash", "INBOX", &mids(&["<a@x>", "<stuck@x>", "<b@x>"]));
    assert_eq!(back, Ok(vec![true, false, true]));
    assert_eq!(mail.holds("Trash"), ["<stuck@x>"]);
    let logins = mail.mail().asked.iter().filter(|a| a.starts_with("LOGIN")).count();
    assert_eq!(logins, 1);
}

#[test]
fn a_lost_connection_is_an_error_and_hangs_up() {
    let mail = Shared::with(&[("Trash", &["<a@x>"])]);
    mail.mail().lost = true;
    let back = ops(&mail).move_back_many(ADA, "Trash", "INBOX", &mids(&["<a@x>"]));
    assert_eq!(back, Err(ServerError::Unreachable("abort")));
    assert_eq!(mail.mail().asked.last().map(String::as_str), Some("HANG UP"));
}
