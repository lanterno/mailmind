//! What happens to a snooze, a mute, a follow-up or a task (`mailmind.attention.domain.events`), said once it has
//! committed. The program hears each through the `Events` port: it tells the open tabs and moves the counts it keeps.

/// What Attention says happened.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum AttentionEvent {
    /// A request hid these many messages until later.
    SnoozeSet {
        count: usize,
    },
    /// A request brought these many back before their time.
    SnoozeLifted {
        count: usize,
    },
    /// These many came back on time, in these accounts, each named once, in order.
    SnoozeWoke {
        count: usize,
        accounts: Vec<String>,
    },
    ThreadMuted {
        account: String,
        thread_id: String,
    },
    ThreadUnmuted {
        account: String,
        thread_id: String,
    },
    /// The minute's round changed which threads are due, a swap included: `count` are due now.
    FollowUpsChanged {
        count: usize,
    },
    /// A person dismissed the thread's follow-up, whether or not it had one.
    FollowUpDismissed {
        account: String,
        thread_id: String,
    },
    /// A person pinned the thread to Reply Later, or took it off.
    ReplyLaterChanged {
        account: String,
        thread_id: String,
    },
    /// A task someone confirmed was added, marked or removed.
    TasksChanged,
}
