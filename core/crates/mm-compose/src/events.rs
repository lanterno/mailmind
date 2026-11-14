//! What Compose says happened (`compose.domain.events`): the server tells the open tabs, and lets go of the counts
//! a send moved.

/// An event of Compose's, published through `sending_ports::Events`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ComposeEvent {
    /// A queued send went out, or a later try at what it still owed ended.
    Sent { outbox_id: i64, account: String },
    /// A try at a queued send failed: it waits for its next try, or, `gave_up`, in error for a person to retry it.
    Failed { outbox_id: i64, account: String, gave_up: bool },
    /// A sent message's draft left the server and the list.
    DraftCopyRemoved { account: String, folder: String },
}
