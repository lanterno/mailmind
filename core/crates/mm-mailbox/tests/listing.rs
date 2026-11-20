//! What a list's row says the AI's verdict asks of you (`mailmind.mailbox.domain.listing`): a message read as junk asks
//! nothing, whatever the AI said of it before it was, as the brief leaves it out.

use mm_kernel::seams::Moment;
use mm_kernel::zone::Utc;
use mm_mailbox::listing::{listed_item, snoozed_item};
use mm_mailbox::ports::Row;
use serde_json::{Value, json};

// conftest.NOW, 2026-11-02 12:00 UTC.
const NOW: Moment = Moment::from_micros(1_793_620_800_000_000);

/// A message the AI said needs you, a next step and all; `is_junk` as the classifier read it.
fn row(is_junk: i64) -> Row {
    let Value::Object(r) = json!({
        "pk": 1, "message_key": 7, "folder": "INBOX", "uid": 3, "from_name": "Example Bank",
        "from_addr": "alerts@bank.example.test", "subject": "New sign-in to your account", "date_iso": "2026-11-01",
        "snippet": "Was this you?", "seen": 0, "flagged": 0, "has_attach": 0, "is_junk": is_junk,
        "bucket": if is_junk == 1 { "blocked" } else { "notif" }, "importance": 4, "category": "action-needed",
        "action": "Check the sign-in", "thread_id": "rf:1", "summary": "A sign-in from a new device.", "ts": 100,
        "account": "bea@example.test", "snoozed_until": 1_793_700_000,
    }) else {
        unreachable!()
    };
    r
}

fn asks(item: &Value) -> [bool; 4] {
    ["needs_you", "reply_worthy", "commitment_worthy", "safe_to_file"].map(|name| item[name] == json!(true))
}

#[test]
fn a_row_carries_what_the_verdict_asks_of_you() {
    assert_eq!(asks(&listed_item(&row(0), false, NOW, &Utc)), [true, true, true, false]);
    assert_eq!(asks(&listed_item(&row(0), true, NOW, &Utc)), [true, true, true, false]);
    assert_eq!(asks(&snoozed_item(&row(0), NOW)), [true, true, true, false]);
}

#[test]
fn a_row_read_as_junk_asks_nothing_though_the_ai_said_it_needed_you_before() {
    for grouped in [false, true] {
        let item = listed_item(&row(1), grouped, NOW, &Utc);
        assert_eq!(asks(&item), [false; 4]);
        // What the AI said stays to be read, and needs you again if the sender's rule goes.
        assert_eq!(item["action"], json!("Check the sign-in"));
    }
    assert_eq!(asks(&snoozed_item(&row(1), NOW)), [false; 4]);
}
