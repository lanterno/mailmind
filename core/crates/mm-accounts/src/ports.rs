//! What the Accounts use cases need of the world.

use mm_kernel::errors::Fault;
use serde_json::{Map, Value};

/// `mailmind.config.json`: the accounts, where each one's secret is, and the top-level settings.
pub trait ConfigFile: Send + Sync + std::fmt::Debug {
    /// The file's contents, a copy of the caller's own, or `{"accounts": []}` when there is no file. A file that
    /// isn't a JSON object is a fault, every time it is read.
    fn read(&self) -> Result<Map<String, Value>, Fault>;

    /// Replace the file with `raw`, atomically and readable by its owner only.
    fn write(&self, raw: &Map<String, Value>) -> Result<(), Fault>;
}
