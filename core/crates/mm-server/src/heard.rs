//! What the contexts say happened, heard (`bootstrap.wiring`'s subscriptions): each told to the open tabs under the
//! name the Python app gives it, and the counts `/api/stats` keeps let go when it moved them.

use std::sync::Arc;

use mm_attention::events::AttentionEvent;
use mm_compose::events::ComposeEvent;
use mm_kernel::events::{Events as _, Topic};
use mm_kernel::log;
use mm_mailbox::folders::Folders;
use mm_mailbox::stats::LiveStats;
use mm_mailbox::sync_ports::MailboxEvent;
use serde_json::json;

use crate::events::Hub;

const TAG: &str = "events";

/// Where the Mailbox's and Attention's events go: the hub, and the counts and badges they move.
#[derive(Debug)]
pub(crate) struct Heard {
    pub(crate) hub: Arc<Hub>,
    pub(crate) stats: Arc<LiveStats>,
    pub(crate) folders: Arc<Folders>,
}

impl mm_mailbox::sync_ports::Events for Heard {
    fn publish(&self, event: MailboxEvent) {
        match event {
            // Mail read or starred elsewhere shows on a list that's open.
            MailboxEvent::FlagsChanged { account, folder, changed } => {
                self.hub.publish(Topic::FlagsChanged, json!({"account": account, "folder": folder, "count": changed}));
            }
            // A queued change landed, or won't: the counts and lists may differ (`filing.settled`).
            MailboxEvent::Applied { .. } | MailboxEvent::Restored { .. } => {
                self.stats.invalidate();
                self.hub.publish(Topic::CommandsChanged, json!({}));
            }
            MailboxEvent::Page { topic, data } => {
                if topic == Topic::UndoDone {
                    // Undo put a message back or took one away: the counts, and the rail's badges.
                    self.stats.invalidate();
                    if let Err(fault) = self.folders.invalidate(None) {
                        log::warning(TAG, "the rail's counts kept couldn't go", &[], Some(fault.class));
                    }
                }
                self.hub.publish(topic, data);
            }
        }
    }
}

impl mm_attention::ports::Events for Heard {
    fn publish(&self, event: AttentionEvent) {
        match event {
            AttentionEvent::SnoozeSet { .. } | AttentionEvent::SnoozeLifted { .. } => self.stats.invalidate(),
            // Snoozed mail pops back into an open inbox.
            AttentionEvent::SnoozeWoke { count, accounts } => {
                self.stats.invalidate();
                self.hub.publish(Topic::SnoozeWake, json!({"count": count, "accounts": accounts}));
            }
            AttentionEvent::ThreadMuted { account, .. } | AttentionEvent::ThreadUnmuted { account, .. } => {
                self.stats.invalidate();
                self.hub.publish(Topic::Mute, json!({"account": account}));
            }
            AttentionEvent::FollowUpsChanged { count } => {
                self.stats.invalidate();
                self.hub.publish(Topic::Followup, json!({"count": count}));
            }
            AttentionEvent::FollowUpDismissed { account, .. } => {
                self.stats.invalidate();
                self.hub.publish(Topic::Followup, json!({"account": account}));
            }
            AttentionEvent::ReplyLaterChanged { account, .. } => {
                self.hub.publish(Topic::Followup, json!({"account": account}));
            }
            AttentionEvent::TasksChanged => self.hub.publish(Topic::TasksChanged, json!({})),
        }
    }
}

impl mm_compose::sending_ports::Events for Heard {
    fn publish(&self, event: ComposeEvent) {
        match event {
            // A queued send went out or failed a try: the tray and the counts (`wiring._after_send`).
            ComposeEvent::Sent { .. } | ComposeEvent::Failed { .. } => {
                self.stats.invalidate();
                self.hub.publish(Topic::Outbox, json!({}));
            }
            // A send's draft left the server: a list of its folder on screen loads again (`folders.list_changed`).
            ComposeEvent::DraftCopyRemoved { account, folder } => {
                self.hub.publish(Topic::Reconcile, json!({"account": account, "folder": folder, "pruned": 1}));
            }
        }
    }
}
