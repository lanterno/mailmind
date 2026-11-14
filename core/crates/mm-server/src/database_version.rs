//! Whether the database is one this server can read: a database the Python app's migrations brought to the version
//! the core is written for (`mm_store::schema::VERSION`). Until the core runs the migrations, it opens no other: an
//! older one lacks what the routes read and answers wrong or not at all, and a newer one may hold what they would undo.

use mm_store::Database;
use mm_store::schema::VERSION;

/// Nothing when the database is at `VERSION`; otherwise what to do, in words a person can act on.
pub(crate) fn fits(database: &Database) -> Result<(), String> {
    match database.version() {
        Ok(Some(version)) if version == VERSION => Ok(()),
        Ok(None) => Err("there is no database yet: start the Python app once (`just run`) to make it".into()),
        Ok(Some(version)) if version < VERSION => Err(format!(
            "the database is at version {version}, and this server reads version {VERSION}: start the Python app \
             once (`just run`) to upgrade it"
        )),
        Ok(Some(version)) => Err(format!(
            "the database is at version {version}, newer than the version {VERSION} this server reads: the Python \
             app has moved on, and this server must catch up before it opens it"
        )),
        Err(fault) if fault.class == "KeyDoesNotFit" => Err("the database key doesn't open the database".into()),
        Err(_) => Err("the database can't be read".into()),
    }
}
