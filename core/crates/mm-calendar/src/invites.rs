//! The invite a message carries, for its card and for an answer to it (`mailmind.calendar.application.invites`).

use std::sync::Arc;

use mm_content::mime::Attachment;
use mm_kernel::zone::Zone;
use serde_json::{Value, json};

use crate::invite::{self, Invite};
use crate::ports::{Here, ZoneNames};

/// The invite a message's card shows, and the part that holds it: each None where the message carries none. A
/// calendar part that won't read is none; one that holds no event is a part with no invite.
pub fn card(parts: &[Attachment], raw: &[u8], here: Here<'_>) -> (Option<Invite>, Option<u32>) {
    let Some(part) = parts
        .iter()
        .find(|a| a.content_type.to_lowercase().contains("calendar") || a.filename.to_lowercase().ends_with(".ics"))
    else {
        return (None, None);
    };
    if raw.is_empty() {
        return (None, None);
    }
    match mm_content::mime::extract_part(raw, part.part_id) {
        Some((payload, _)) => (invite::parse(&String::from_utf8_lossy(&payload), here), Some(part.part_id)),
        None => (None, None),
    }
}

/// The card's invite of a message as it opens, read in the person's zone.
#[derive(Debug)]
pub struct Invites {
    zones: Arc<dyn ZoneNames>,
    person: Arc<dyn Zone>,
}

impl Invites {
    pub fn new(zones: Arc<dyn ZoneNames>, person: Arc<dyn Zone>) -> Invites {
        Invites { zones, person }
    }

    /// The invite and the id of the part that holds it, as the opened message carries them: each null where it
    /// carries none.
    pub fn of(&self, parts: &[Attachment], raw: &[u8]) -> (Value, Value) {
        let here = Here { zones: &*self.zones, person: &*self.person };
        let (invite, part) = card(parts, raw, here);
        (invite.as_ref().map_or(Value::Null, Invite::to_json), part.map_or(Value::Null, |p| json!(p.to_string())))
    }
}
