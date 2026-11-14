//! What judging a person's filters of new mail needs (`ports.Conditions`' `holds`): whether a stored message is one a
//! search finds, which is Discovery's to say. The program implements it.

use std::fmt;

use mm_kernel::errors::Fault;

pub trait Judge: Send + Sync + fmt::Debug {
    /// For each of `queries`, whether the stored message `pk` is one it finds. A query the index rejects finds
    /// nothing; a store that can't be read is a fault.
    fn holds(&self, pk: i64, queries: &[&str]) -> Result<Vec<bool>, Fault>;
}
