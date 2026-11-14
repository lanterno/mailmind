//! What the use cases build on a mail server: a move by MOVE or by copy, the folder playing a role, the newest copy of
//! a Message-ID, and a read-only process's session.

#![allow(clippy::unwrap_used)]

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

use mm_kernel::errors::Fault;
use mm_mailbox::ports::{MailSession, SessionError};
use mm_mailbox::roles::Folder;
use mm_mailbox::session::{
    Headers, MailServer, ReadOnly, Search, Selected, ServerError, by_message_id, find_role, found_by_message_id,
    move_by_copy, move_to,
};

/// A server that writes down what it was asked, and refuses what `refuse` names.
#[derive(Debug, Default)]
struct Recording {
    capabilities: Vec<String>,
    refuse: Vec<&'static str>,
    lost: Vec<&'static str>,
    found: Vec<u32>,
    folders: Vec<Folder>,
    asked: Arc<Mutex<Vec<String>>>,
}

impl Recording {
    fn with(capabilities: &[&str]) -> Recording {
        Recording { capabilities: capabilities.iter().map(|c| (*c).to_owned()).collect(), ..Recording::default() }
    }

    fn answer(&mut self, what: &'static str, said: String) -> Result<(), ServerError> {
        self.asked.lock().unwrap().push(said);
        if self.lost.contains(&what) {
            return Err(ServerError::Unreachable("abort"));
        }
        if self.refuse.contains(&what) {
            return Err(ServerError::Refused("error"));
        }
        Ok(())
    }
}

impl MailSession for Recording {
    fn folders(&mut self) -> Result<Vec<Folder>, SessionError> {
        self.asked.lock().unwrap().push("LIST".to_owned());
        if self.lost.contains(&"LIST") {
            return Err(SessionError::Unreachable("abort"));
        }
        if self.refuse.contains(&"LIST") {
            return Err(SessionError::Failed(Fault::new("error")));
        }
        Ok(self.folders.clone())
    }

    fn status(&mut self, _folder: &str) -> Result<(i64, i64), SessionError> {
        Ok((0, 0))
    }

    fn logout(&mut self) {
        self.asked.lock().unwrap().push("LOGOUT".to_owned());
    }
}

impl MailServer for Recording {
    fn capabilities(&self) -> Vec<String> {
        self.capabilities.clone()
    }

    fn select(&mut self, folder: &str, readonly: bool) -> Result<Selected, ServerError> {
        self.answer("SELECT", format!("{} {folder}", if readonly { "EXAMINE" } else { "SELECT" }))?;
        Ok(Selected::default())
    }

    fn search(&mut self, criteria: Search<'_>) -> Result<Vec<u32>, ServerError> {
        self.answer("SEARCH", format!("SEARCH {criteria:?}"))?;
        Ok(self.found.clone())
    }

    fn flags(&mut self, _uids: &[u32]) -> Result<BTreeMap<u32, Vec<String>>, ServerError> {
        Ok(BTreeMap::new())
    }

    fn enable_condstore(&mut self) -> Result<bool, ServerError> {
        Ok(false)
    }

    fn headers(&mut self, _uids: &[u32], _fields: &[&str], _gmail: bool) -> Result<Vec<Headers>, ServerError> {
        Ok(Vec::new())
    }

    fn body(&mut self, _uid: u32) -> Result<Option<Vec<u8>>, ServerError> {
        Ok(None)
    }

    fn arrivals(&mut self, uids: &[u32]) -> Result<BTreeMap<u32, i64>, ServerError> {
        self.answer("FETCH", format!("INTERNALDATE {uids:?}"))?;
        Ok(uids.iter().map(|&uid| (uid, 1_767_268_800 + i64::from(uid))).collect())
    }

    fn store_flags(&mut self, uids: &[u32], add: bool, flags: &[&str]) -> Result<(), ServerError> {
        self.answer("STORE", format!("STORE {uids:?} {}{flags:?}", if add { '+' } else { '-' }))
    }

    fn store_labels(&mut self, uids: &[u32], add: bool, labels: &[&str]) -> Result<(), ServerError> {
        self.answer("STORE", format!("LABELS {uids:?} {}{labels:?}", if add { '+' } else { '-' }))
    }

    fn copy(&mut self, uids: &[u32], dest: &str) -> Result<(), ServerError> {
        self.answer("COPY", format!("COPY {uids:?} {dest}"))
    }

    fn uid_move(&mut self, uids: &[u32], dest: &str) -> Result<(), ServerError> {
        self.answer("MOVE", format!("MOVE {uids:?} {dest}"))
    }

    fn expunge(&mut self, uids: &[u32]) -> Result<(), ServerError> {
        self.answer("EXPUNGE", format!("EXPUNGE {uids:?}"))
    }

    fn append(&mut self, folder: &str, _raw: &[u8], _: &[&str], _: Option<&str>) -> Result<Option<u32>, ServerError> {
        self.answer("APPEND", format!("APPEND {folder}"))?;
        Ok(Some(7))
    }

    fn shutdown(&mut self) {
        self.asked.lock().unwrap().push("SHUTDOWN".to_owned());
    }
}

#[test]
fn a_server_with_move_gets_one_move() {
    let mut server = Recording::with(&["MOVE", "UIDPLUS"]);
    move_to(&mut server, &[5, 6], "Archive").unwrap();
    assert_eq!(*server.asked.lock().unwrap(), ["MOVE [5, 6] Archive"]);
}

#[test]
fn a_server_without_move_gets_copy_then_deleted_then_an_expunge_of_each_uid() {
    let mut server = Recording::with(&["UIDPLUS"]);
    move_to(&mut server, &[5, 6], "Archive").unwrap();
    assert_eq!(
        *server.asked.lock().unwrap(),
        ["COPY [5, 6] Archive", "STORE [5, 6] +[\"\\\\Deleted\"]", "EXPUNGE [5]", "EXPUNGE [6]"]
    );
}

#[test]
fn a_refused_move_is_made_by_copy() {
    let mut server = Recording { refuse: vec!["MOVE"], ..Recording::with(&["MOVE"]) };
    move_to(&mut server, &[5], "Archive").unwrap();
    assert_eq!(
        *server.asked.lock().unwrap(),
        ["MOVE [5] Archive", "COPY [5] Archive", "STORE [5] +[\"\\\\Deleted\"]", "EXPUNGE [5]"]
    );
}

#[test]
fn a_move_lost_on_the_way_is_not_tried_again_by_copy() {
    let mut server = Recording { lost: vec!["MOVE"], ..Recording::with(&["MOVE"]) };
    assert_eq!(move_to(&mut server, &[5], "Archive"), Err(ServerError::Unreachable("abort")));
    assert_eq!(*server.asked.lock().unwrap(), ["MOVE [5] Archive"]);
}

#[test]
fn a_refused_copy_touches_no_original() {
    let mut server = Recording { refuse: vec!["COPY"], ..Recording::default() };
    assert_eq!(move_by_copy(&mut server, &[5], "Archive"), Err(ServerError::Refused("error")));
    assert_eq!(*server.asked.lock().unwrap(), ["COPY [5] Archive"]);
}

#[test]
fn a_refused_uid_expunge_leaves_the_original_flagged_deleted() {
    let mut server = Recording { refuse: vec!["EXPUNGE"], ..Recording::default() };
    move_by_copy(&mut server, &[5, 6], "Archive").unwrap();
    assert_eq!(server.asked.lock().unwrap().len(), 4);
}

#[test]
fn the_newest_copy_of_a_message_id_is_found_and_none_without_one() {
    let mut server = Recording { found: vec![3, 9, 4], ..Recording::default() };
    assert_eq!(by_message_id(&mut server, "<a@example.test>"), Ok(Some(9)));
    assert_eq!(by_message_id(&mut server, ""), Ok(None));
    assert_eq!(*server.asked.lock().unwrap(), ["SEARCH MessageId(\"<a@example.test>\")"]);

    let mut refusing = Recording { refuse: vec!["SEARCH"], ..Recording::default() };
    assert_eq!(found_by_message_id(&mut refusing, "<a@example.test>"), Ok(None));
    let mut lost = Recording { lost: vec!["SEARCH"], ..Recording::default() };
    assert_eq!(found_by_message_id(&mut lost, "<a@example.test>"), Err(ServerError::Unreachable("abort")));
}

fn folder(name: &str, attributes: &[&str]) -> Folder {
    Folder {
        name: name.to_owned(),
        attributes: attributes.iter().map(|a| (*a).to_owned()).collect(),
        delimiter: Some("/".to_owned()),
    }
}

#[test]
fn the_folder_playing_a_role_is_found_and_a_refused_list_finds_none() {
    let folders = vec![folder("INBOX", &[]), folder("Bin", &["\\trash"])];
    let mut server = Recording { folders, ..Recording::default() };
    assert_eq!(find_role(&mut server, &["trash"]), Ok(Some("Bin".to_owned())));
    assert_eq!(find_role(&mut server, &["junk"]), Ok(None));
    let mut refusing = Recording { refuse: vec!["LIST"], ..Recording::default() };
    assert_eq!(find_role(&mut refusing, &["trash"]), Ok(None));
    let mut lost = Recording { lost: vec!["LIST"], ..Recording::default() };
    assert_eq!(find_role(&mut lost, &["trash"]), Err(ServerError::Unreachable("abort")));
}

#[test]
fn a_read_only_session_examines_and_sends_no_write() {
    let server = Recording::with(&["MOVE"]);
    let heard = Arc::clone(&server.asked);
    let mut session = ReadOnly(Box::new(server));
    session.select("INBOX", false).unwrap();
    let unreachable = Err(ServerError::Unreachable("ReadOnlyError"));
    assert_eq!(session.store_flags(&[1], true, &["\\Seen"]), unreachable);
    assert_eq!(session.store_labels(&[1], false, &["\\Inbox"]), unreachable);
    assert_eq!(session.copy(&[1], "Archive"), unreachable);
    assert_eq!(move_to(&mut session, &[1], "Archive"), unreachable);
    assert_eq!(session.expunge(&[1]), unreachable);
    assert_eq!(session.append("Drafts", b"", &[], None), Err(ServerError::Unreachable("ReadOnlyError")));
    assert!(ServerError::Unreachable("ReadOnlyError").is_unreachable());
    assert_eq!(*heard.lock().unwrap(), ["EXAMINE INBOX"]);
}

#[test]
fn a_read_only_session_still_searches_by_sender_and_says_when_mail_arrived() {
    let server = Recording { found: vec![3, 4], ..Recording::with(&[]) };
    let heard = Arc::clone(&server.asked);
    let mut session = ReadOnly(Box::new(server));
    assert_eq!(session.search(Search::From("notify-example.test")).unwrap(), [3, 4]);
    assert_eq!(session.arrivals(&[3, 4]).unwrap(), BTreeMap::from([(3, 1_767_268_803), (4, 1_767_268_804)]));
    assert_eq!(*heard.lock().unwrap(), ["SEARCH From(\"notify-example.test\")", "INTERNALDATE [3, 4]"]);
}
