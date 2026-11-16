//! The database as this server opens it (`store.init`): its key, made with the database when there is neither, and
//! the database itself, made over a data directory with none and brought to the newest version by the core's
//! migrations (`mm_store::migrate`). One the core can't upgrade (older than the Python app's version 51, or newer than
//! the core) is refused, in words a person can act on.

use std::path::Path;

use mm_accounts::credential::DATABASE_KEY_ITEM;
use mm_kernel::seams::{Clock, Keychain, Outside, Secret};
use mm_store::migrate::{BASE, NotReady, SQLITE_NEEDED};
use mm_store::schema::VERSION;
use mm_store::{Database, DbKey};

use crate::app::Seams;
use crate::environment::Environment;

/// What `db check-upgrade`, which only a developer runs, says of a key it can't find.
const NO_KEY: &str = "no database key: MAILMIND_DB_KEY isn't set, and the Keychain can't give one";
/// What a start says of a key it can't have, in a person's words, since the Mac app's window shows them as they are:
/// the database is there and the Keychain has no key to it, or didn't answer, or wouldn't keep a new one; or what it
/// holds isn't a key.
const NOT_KEPT: &str = "MailMind's key to this Mac's copy of your mail isn't in your Keychain, so the copy can't be \
                        read. Start Fresh makes a new copy from your mail's servers.";
const NOT_GIVEN: &str = "Your Keychain didn't give MailMind the key to this Mac's copy of your mail. Unlock your \
                         login Keychain when macOS asks, then open MailMind again.";
const NOT_TAKEN: &str = "Your Keychain wouldn't keep MailMind's key, so no copy of your mail was made on this Mac. \
                         Unlock your login Keychain when macOS asks, then open MailMind again.";
const NOT_A_KEY: &str = "What your Keychain holds as MailMind's key to this Mac's copy of your mail isn't one, so the \
                         copy can't be read. Start Fresh makes a new copy from your mail's servers.";
/// What MAILMIND_DB_KEY holds isn't a key: only a developer, a test or the demo sets it.
const NOT_A_VARIABLE_KEY: &str = "MAILMIND_DB_KEY isn't 64 hex digits.";

/// The database's key: MAILMIND_DB_KEY's, else the Keychain's. With neither, and no database yet, a new one, kept in
/// the Keychain first (`key.ensure`). A database that is there and has no key stays unread: a key made for it would
/// open nothing.
pub(crate) fn key(environment: &Environment, path: &Path, seams: &Seams) -> Result<DbKey, String> {
    if let Some(given) = environment.db_key() {
        return DbKey::from_hex(given.expose()).map_err(|_| NOT_A_VARIABLE_KEY.into());
    }
    let held = match seams.keychain.find(DATABASE_KEY_ITEM) {
        Ok(Some(key)) => key,
        Ok(None) if no_database(path, &*seams.outside) => made(&*seams.keychain)?,
        Ok(None) => return Err(NOT_KEPT.into()),
        Err(_) => return Err(NOT_GIVEN.into()),
    };
    DbKey::from_hex(held.expose()).map_err(|_| NOT_A_KEY.into())
}

/// `said`, a start's refusal, as `mailmind-server` says it on the command line: a key's words with where a developer
/// gives one in the Keychain's place.
pub fn on_the_command_line(said: &str) -> String {
    if [NOT_KEPT, NOT_GIVEN, NOT_TAKEN, NOT_A_KEY].contains(&said) {
        format!("{said} (MAILMIND_DB_KEY, when it is set, gives the key in the Keychain's place.)")
    } else {
        said.to_owned()
    }
}

/// The database's key as `key` finds it, but never made: for what only reads a database (`db check-upgrade`).
pub(crate) fn existing_key(environment: &Environment, seams: &Seams) -> Result<DbKey, String> {
    let held = match environment.db_key() {
        Some(key) => key,
        None => seams.keychain.find(DATABASE_KEY_ITEM).ok().flatten().ok_or(NO_KEY)?,
    };
    DbKey::from_hex(held.expose()).map_err(|_| "the database key isn't 64 hex digits".into())
}

/// Whether there is no database at `path` yet: no file, or an empty one.
fn no_database(path: &Path, outside: &dyn Outside) -> bool {
    outside.open(path).is_ok() && std::fs::metadata(path).map_or(true, |found| found.len() == 0)
}

/// A new key from the system's random source, kept in the Keychain. What the Keychain holds once it is kept is the
/// key, whoever put it there.
fn made(keychain: &dyn Keychain) -> Result<Secret, String> {
    let key = DbKey::filled(getrandom::fill).map_err(|_| "the system's random source failed")?;
    keychain.put(DATABASE_KEY_ITEM, &key.hex()).map_err(|_| NOT_TAKEN)?;
    keychain.find(DATABASE_KEY_ITEM).ok().flatten().ok_or_else(|| NOT_TAKEN.into())
}

/// Nothing when the database is ready: made if there was none, and upgraded to `VERSION`, a copy taken first and
/// named for `clock`'s time. Otherwise what to do.
pub(crate) fn ready(database: &Database, clock: &dyn Clock) -> Result<(), String> {
    database.ready(clock).map(|_| ()).map_err(|not| said(&not))
}

/// What a person is told of a database that isn't ready: the Mac app's window shows it as it is, so it names what
/// to do in MailMind's own words, never a developer's program or command, in sentences as the window sets them.
fn said(not: &NotReady) -> String {
    let (major, minor) = SQLITE_NEEDED;
    match not {
        NotReady::SqliteTooOld(found) => {
            format!("MailMind needs SQLite {major}.{minor} or newer, and this build has {found}. Nothing was changed.")
        }
        NotReady::Older(version) => format!(
            "This Mac's copy of your mail is at version {version}, older than version {BASE}, the oldest this \
             MailMind can upgrade, so it was left as it is. Start Fresh makes a new copy from your mail's servers."
        ),
        NotReady::Newer(version) => format!(
            "This Mac's copy of your mail is at version {version}, made by a newer MailMind than this one, which \
             reads up to version {VERSION}: install the newest MailMind to open it."
        ),
        NotReady::KeyDoesNotFit => "MailMind's key doesn't open this Mac's copy of your mail, so the copy can't be \
                                    read. Start Fresh makes a new copy from your mail's servers."
            .into(),
        NotReady::Tripped | NotReady::Storage => "MailMind can't make or read this Mac's copy of your mail. Check \
                                                  that your Mac's disk has room, then open MailMind again."
            .into(),
        NotReady::NoBackup => "This Mac's copy of your mail couldn't be backed up before its upgrade, so it wasn't \
                               upgraded. Check that your Mac's disk has room, then open MailMind again."
            .into(),
        NotReady::Failed(version) => format!(
            "The upgrade of this Mac's copy of your mail to version {version} failed, and the copy is as it was \
             before. Open MailMind again to try it again."
        ),
    }
}
