//! What Discovery's use cases need of the world.

use std::fmt;

use mm_kernel::errors::Fault;

// The Document Hub's (area B).

/// A stored row, by column name, each value as SQLite holds it.
pub type Row = serde_json::Map<String, serde_json::Value>;

/// The accounts every corpus-wide read goes over, by address.
pub trait AccountNames: Send + Sync + fmt::Debug {
    fn emails(&self) -> Result<Vec<String>, Fault>;
}

/// The attachments stored of the mail of `accounts`: each that isn't inline and has a name, of a copy not on its
/// way out, the newest message first and then in the order they were stored.
pub trait StoredDocuments: Send + Sync + fmt::Debug {
    fn attachments(&self, accounts: &[String]) -> Result<Vec<Row>, Fault>;
}

/// What the mail says of one sender, by their lowercase address (`tally`): `n` messages, `replied` of them
/// answered, the `first` and `last` times one came, and the `name` they wrote under.
pub trait StoredTally: Send + Sync + fmt::Debug {
    fn tally(&self, address: &str) -> Result<Row, Fault>;
}
