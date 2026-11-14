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

const NO_KEY: &str = "no database key: MAILMIND_DB_KEY isn't set, and the Keychain can't give one";

/// The database's key: MAILMIND_DB_KEY's, else the Keychain's. With neither, and no database yet, a new one, kept in
/// the Keychain first (`key.ensure`). A database that is there and has no key stays unread: a key made for it would
/// open nothing.
pub(crate) fn key(environment: &Environment, path: &Path, seams: &Seams) -> Result<DbKey, String> {
    let held = match environment.db_key() {
        Some(key) => key,
        None => match seams.keychain.find(DATABASE_KEY_ITEM) {
            Ok(Some(key)) => key,
            Ok(None) if no_database(path, &*seams.outside) => made(&*seams.keychain)?,
            _ => return Err(NO_KEY.into()),
        },
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
    keychain
        .put(DATABASE_KEY_ITEM, &key.hex())
        .map_err(|_| "no database key: MAILMIND_DB_KEY isn't set, and the Keychain wouldn't keep a new one")?;
    keychain.find(DATABASE_KEY_ITEM).ok().flatten().ok_or_else(|| NO_KEY.into())
}

/// Nothing when the database is ready: made if there was none, and upgraded to `VERSION`, a copy taken first and
/// named for `clock`'s time. Otherwise what to do.
pub(crate) fn ready(database: &Database, clock: &dyn Clock) -> Result<(), String> {
    database.ready(clock).map(|_| ()).map_err(|not| said(&not))
}

/// What a person is told of a database that isn't ready.
pub(crate) fn said(not: &NotReady) -> String {
    let (major, minor) = SQLITE_NEEDED;
    match not {
        NotReady::SqliteTooOld(found) => {
            format!("MailMind needs SQLite {major}.{minor} or newer, and this build has {found}. Nothing was changed.")
        }
        NotReady::Older(version) => format!(
            "the database is at version {version}, older than the version {BASE} this server upgrades from: start \
             the Python app once (`just run`) to upgrade it"
        ),
        NotReady::Newer(version) => format!(
            "the database is at version {version}, newer than the version {VERSION} this server reads: the Python \
             app has moved on, and this server must catch up before it opens it"
        ),
        NotReady::KeyDoesNotFit => "the database key doesn't open the database".into(),
        NotReady::Tripped | NotReady::Storage => "the database can't be made or read".into(),
        NotReady::NoBackup => "the database couldn't be backed up before its upgrade, so it wasn't upgraded".into(),
        NotReady::Failed(version) => format!(
            "the database's upgrade to version {version} failed: it is as it was before it, and the next start tries \
             it again"
        ),
    }
}
