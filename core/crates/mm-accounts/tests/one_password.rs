//! A secret kept in 1Password (`one_password`, and the Directory's reads of it), through a stand-in for op that
//! answers each reference from its list in turn, the last for ever after, as tests/unit/accounts/test_secrets.py's
//! does: a read kept for the process once it reads, its spaces taken out for an account that asks, each reference
//! tried in turn, what an account's change or removal drops, the reads ahead of the first sync, and a reference that
//! isn't one never sent. The program itself is mm-server's tests' (tests/one_password.rs).

#![allow(clippy::unwrap_used)]

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use mm_accounts::Directory;
use mm_accounts::connections::quiet;
use mm_accounts::one_password::{OpReads, UNREAD, readable};
use mm_accounts::ports::{ConfigFile, LoginTrouble, OnePassword};
use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Keychain, KeychainProblem, Moment, Secret};
use serde_json::{Map, Value, json};

const REF: &str = "op://Private/Ada/password";
const OTHER: &str = "op://Private/Ada/old";
const ADA: &str = "ada@example.test";

/// `op read`, answering each reference from its list in turn, the last for ever after; what it was asked, in order.
#[derive(Debug, Default)]
struct Op {
    answers: Mutex<HashMap<String, Vec<Result<&'static str, Fault>>>>,
    asked: Mutex<Vec<String>>,
    /// How long each read takes.
    slow: Duration,
}

impl Op {
    fn answering(answers: &[(&str, &[Result<&'static str, Fault>])]) -> Arc<Op> {
        Arc::new(Op {
            answers: Mutex::new(answers.iter().map(|(r, said)| ((*r).to_owned(), said.to_vec())).collect()),
            ..Op::default()
        })
    }

    fn asked(&self) -> Vec<String> {
        self.asked.lock().unwrap().clone()
    }
}

impl OnePassword for Op {
    fn read(&self, reference: &str) -> Result<Secret, Fault> {
        self.asked.lock().unwrap().push(reference.to_owned());
        std::thread::sleep(self.slow);
        let mut answers = self.answers.lock().unwrap();
        let said = answers.get_mut(reference).unwrap();
        let answer = if said.len() > 1 { said.remove(0) } else { said[0] };
        answer.map(|printed| Secret::new(printed.to_owned()))
    }
}

fn reads(op: &Arc<Op>) -> OpReads {
    OpReads::new(op.clone())
}

#[test]
fn a_read_is_what_op_printed_trimmed_and_is_kept_for_the_process() {
    let op = Op::answering(&[(REF, &[Ok("  hunter2 \n")])]);
    let reads = reads(&op);
    assert_eq!(reads.read(&json!(REF), false).unwrap().expose(), "hunter2");
    assert_eq!(reads.read(&json!(REF), false).unwrap().expose(), "hunter2");
    assert_eq!(op.asked(), [REF]);
}

#[test]
fn strip_takes_the_spaces_out_of_an_app_password() {
    let op = Op::answering(&[(REF, &[Ok("abcd efgh ijkl mnop\n")])]);
    assert_eq!(reads(&op).read(&json!(REF), true).unwrap().expose(), "abcdefghijklmnop");
}

#[test]
fn a_failed_read_is_not_kept() {
    let op = Op::answering(&[(REF, &[Err(UNREAD), Ok("hunter2")])]);
    let reads = reads(&op);
    assert_eq!(reads.read(&json!(REF), false).unwrap_err(), UNREAD);
    assert_eq!(reads.read(&json!(REF), false).unwrap().expose(), "hunter2");
    assert_eq!(op.asked(), [REF, REF]);
}

#[test]
fn each_reference_is_tried_in_turn_until_one_reads() {
    let op = Op::answering(&[(OTHER, &[Err(UNREAD)]), (REF, &[Ok("hunter2")])]);
    let reads = reads(&op);
    assert_eq!(reads.read(&json!([OTHER, REF]), false).unwrap().expose(), "hunter2");
    // The one that read is kept; the one that didn't is asked again.
    assert_eq!(reads.read(&json!([OTHER, REF]), false).unwrap().expose(), "hunter2");
    assert_eq!(op.asked(), [OTHER, REF, OTHER]);
}

#[test]
fn when_no_reference_reads_the_last_fault_is_the_answer() {
    let op = Op::answering(&[(OTHER, &[Err(Fault::new("First"))]), (REF, &[Err(Fault::new("Second"))])]);
    assert_eq!(reads(&op).read(&json!([OTHER, REF]), false).unwrap_err(), Fault::new("Second"));
}

#[test]
fn a_reference_that_isnt_one_is_never_sent() {
    let op = Op::answering(&[]);
    let reads = reads(&op);
    for named in [json!(""), json!("--out-file=/tmp/x"), json!("-h"), json!("op://Private/Ada\npassword"), json!(7)] {
        assert_eq!(reads.read(&named, false).unwrap_err(), UNREAD, "{named}");
    }
    assert_eq!(reads.read(&json!([]), false).unwrap_err(), Fault::new("NoCredential"));
    assert!(op.asked().is_empty());
    assert!(readable(REF) && readable("op://Private/Gmail App/app password?attribute=otp"));
    assert!(!readable("Op://Private/Ada/password") && !readable("op:/Private") && !readable("op://a\u{7f}b"));
}

#[test]
fn evict_drops_what_a_credential_read_so_the_next_read_asks_again() {
    let op = Op::answering(&[(REF, &[Ok("old")]), (OTHER, &[Ok("other")])]);
    let reads = reads(&op);
    reads.read(&json!(REF), false).unwrap();
    reads.read(&json!(OTHER), false).unwrap();
    reads.evict(&json!([REF]));
    reads.read(&json!(REF), false).unwrap();
    reads.read(&json!(OTHER), false).unwrap();
    assert_eq!(op.asked(), [REF, OTHER, REF]);
}

#[test]
fn a_reference_two_logins_want_at_once_is_asked_of_op_once() {
    let op = Arc::new(Op {
        answers: Mutex::new(HashMap::from([(REF.to_owned(), vec![Ok("hunter2")])])),
        slow: Duration::from_millis(100),
        ..Op::default()
    });
    let reads = Arc::new(reads(&op));
    let both: Vec<_> = (0..2)
        .map(|_| {
            let reads = reads.clone();
            std::thread::spawn(move || reads.read(&json!(REF), false).unwrap().expose().to_owned())
        })
        .collect();
    for read in both {
        assert_eq!(read.join().unwrap(), "hunter2");
    }
    assert_eq!(op.asked(), [REF]);
}

#[test]
fn nothing_read_shows_in_a_debug() {
    let op = Op::answering(&[(REF, &[Ok("hunter2")])]);
    let reads = reads(&op);
    let read = reads.read(&json!(REF), false);
    let shown = format!("{reads:?} {read:?}");
    assert!(!shown.contains("hunter2"), "{shown}");
}

#[test]
fn a_failed_read_is_named_to_a_person_as_a_refused_login() {
    assert_eq!(quiet(LoginTrouble("OpReadFailed")), "AUTHENTICATIONFAILED");
}

// -- the Directory's ---------------------------------------------------------------------------------------------

#[derive(Debug)]
struct File(Mutex<Map<String, Value>>);

impl ConfigFile for File {
    fn read(&self) -> Result<Map<String, Value>, Fault> {
        Ok(self.0.lock().unwrap().clone())
    }

    fn write(&self, raw: &Map<String, Value>) -> Result<(), Fault> {
        *self.0.lock().unwrap() = raw.clone();
        Ok(())
    }
}

/// A Keychain that holds Cy's password.
#[derive(Debug)]
struct Held;

impl Keychain for Held {
    fn find(&self, item: &str) -> Result<Option<Secret>, KeychainProblem> {
        Ok((item == "account:cy@example.test").then(|| Secret::new("cy-password".to_owned())))
    }

    fn put(&self, _item: &str, _secret: &Secret) -> Result<(), KeychainProblem> {
        Ok(())
    }

    fn delete(&self, _item: &str) -> Result<bool, KeychainProblem> {
        Ok(false)
    }
}

#[derive(Debug)]
struct At;

impl Clock for At {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000)
    }
}

fn entry(email: &str, kind: &str, credential: Value) -> Value {
    json!({"email": email, "type": kind, "host": "imap.example.test", "port": 993, "credential": credential})
}

fn directory(accounts: Value, op: &Arc<Op>) -> (Directory, Arc<OpReads>) {
    let Value::Object(raw) = json!({"accounts": accounts}) else { unreachable!() };
    let reads = Arc::new(OpReads::new(op.clone()));
    let directory = Directory::new(Arc::new(File(Mutex::new(raw))), Arc::new(Held), Arc::new(At));
    (directory.reading_op(reads.clone()), reads)
}

fn object(value: Value) -> Map<String, Value> {
    match value {
        Value::Object(map) => map,
        _ => unreachable!(),
    }
}

#[test]
fn an_accounts_password_is_read_from_1password_its_spaces_out_when_it_asks() {
    let op = Op::answering(&[(REF, &[Ok("abcd efgh ijkl mnop\n")])]);
    let (directory, _) = directory(json!([entry(ADA, "imap", json!({"op": REF}))]), &op);
    let ada = directory.get(ADA).unwrap().unwrap();
    assert_eq!(directory.password(&ada).unwrap().expose(), "abcd efgh ijkl mnop");
    // A Gmail account takes the spaces out of an app password, as 1Password keeps one.
    let op = Op::answering(&[(REF, &[Ok("abcd efgh ijkl mnop\n")])]);
    let (directory, _) = self::directory(json!([entry(ADA, "gmail", json!({"op": [REF]}))]), &op);
    let ada = directory.get(ADA).unwrap().unwrap();
    assert_eq!(directory.password(&ada).unwrap().expose(), "abcdefghijklmnop");
}

#[test]
fn a_directory_given_no_way_to_read_1password_says_it_cant_yet() {
    let Value::Object(raw) = json!({"accounts": [entry(ADA, "imap", json!({"op": REF}))]}) else { unreachable!() };
    let directory = Directory::new(Arc::new(File(Mutex::new(raw))), Arc::new(Held), Arc::new(At));
    let ada = directory.get(ADA).unwrap().unwrap();
    assert_eq!(directory.password(&ada).unwrap_err(), Fault::new("CredentialStyleNotOnTheRustServerYet"));
}

#[test]
fn a_new_credential_or_a_removal_drops_what_the_old_one_read() {
    let op = Op::answering(&[(REF, &[Ok("first"), Ok("second"), Ok("third")])]);
    let (directory, reads) = directory(json!([entry(ADA, "imap", json!({"op": REF}))]), &op);
    let password = || directory.password(&directory.get(ADA).unwrap().unwrap()).unwrap().expose().to_owned();
    assert_eq!(password(), "first");
    // A change that names no credential keeps what was read.
    directory.update_account(ADA, &object(json!({"alias": "Ada"}))).unwrap();
    assert_eq!(password(), "first");
    // One that names a credential drops it, the same reference too: the person may have changed what it holds.
    directory.update_account(ADA, &object(json!({"credential": {"style": "op", "ref": REF}}))).unwrap();
    assert_eq!(password(), "second");
    directory.remove_account(ADA).unwrap();
    assert_eq!(reads.read(&json!(REF), false).unwrap().expose(), "third");
    assert_eq!(op.asked(), [REF, REF, REF]);
}

#[test]
fn preload_reads_each_enabled_accounts_1password_credential_once_and_counts_those_read() {
    let bea_ref = "op://Private/Bea/password";
    let dee_ref = "op://Private/Dee/password";
    let op = Op::answering(&[(REF, &[Ok("ada-password")]), (bea_ref, &[Err(UNREAD)]), (dee_ref, &[Ok("dee")])]);
    let mut dee = entry("dee@example.test", "imap", json!({"op": dee_ref}));
    dee["disabled"] = json!(true);
    let (directory, _) = directory(
        json!([
            entry(ADA, "imap", json!({"op": REF})),
            entry("bea@example.test", "imap", json!({"op": bea_ref})),
            entry("cy@example.test", "imap", json!({"keychain": "account:cy@example.test"})),
            dee,
        ]),
        &op,
    );
    assert_eq!(directory.preload().unwrap(), 1);
    assert_eq!(op.asked(), [REF, bea_ref]);
    // Ada's login asks nothing more of op.
    assert_eq!(directory.password(&directory.get(ADA).unwrap().unwrap()).unwrap().expose(), "ada-password");
    assert_eq!(op.asked(), [REF, bea_ref]);
}
