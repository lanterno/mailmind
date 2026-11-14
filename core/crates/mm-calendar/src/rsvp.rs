//! An answer to an invite (iMIP, METHOD:REPLY): what it says, how it is keyed, and the use case that sends it to the
//! organizer through the outbox, so it can be undone like any send (`mailmind.calendar.domain.rsvp` and
//! `application.rsvp`).

use std::sync::Arc;

use mm_kernel::errors::{Failure, Problem};
use mm_kernel::seams::Clock;
use mm_kernel::zone::Zone;
use serde_json::{Value, json};

use crate::invite::Invite;
use crate::invites::card;
use crate::ports::{Here, Messages, Reply, StoredInvites, SystemSend, ZoneNames};
use crate::time::{Dt, Tz, fromisoformat};

/// What a person may answer an invite: the decision, its PARTSTAT, and the verb its subject begins with.
pub const DECISIONS: [(&str, &str, &str); 3] = [
    ("accept", "ACCEPTED", "Accepted"),
    ("decline", "DECLINED", "Declined"),
    ("tentative", "TENTATIVE", "Tentatively accepted"),
];

/// The CN the organizer used for me, if any.
pub fn name_for(invite: &Invite, me_email: &str, fallback: &str) -> String {
    invite
        .attendees
        .iter()
        .find(|a| a.email.to_lowercase() == me_email.to_lowercase() && !a.name.is_empty())
        .map_or_else(|| fallback.to_owned(), |a| a.name.clone())
}

pub fn key_prefix(account: &str, invite: &str) -> String {
    format!("rsvp:{account}:{invite}:")
}

/// The same answer again, a double click or a retry, finds the send already queued for it. A changed mind is a new
/// send, even back to an earlier answer, since that one went out long ago. `sent` is the keys so far, newest first.
pub fn key(prefix: &str, sent: &[String], partstat: &str) -> String {
    if let Some(newest) = sent.first()
        && newest.rsplit(':').nth(1) == Some(partstat)
    {
        return newest.clone();
    }
    format!("{prefix}{partstat}:{}", sent.len())
}

/// A value as iCalendar text: a backslash, a semicolon and a comma escaped, a line break `\n`.
pub fn ics_esc(value: &str) -> String {
    value.replace('\\', "\\\\").replace(';', "\\;").replace(',', "\\,").replace('\n', "\\n")
}

/// A name as a parameter, in quotes: one in the name would end them, and RFC 6868's `^'` says it instead.
fn cn(name: &str) -> String {
    if name.is_empty() { String::new() } else { format!(";CN=\"{}\"", ics_esc(name).replace('"', "^'")) }
}

/// RFC 5545's folding: a line of more than 73 bytes goes on, after a line break and a space, from the last whole
/// character that fits.
pub fn fold(line: &str) -> String {
    let mut rest = line;
    let mut out: Vec<&str> = Vec::new();
    while rest.len() > 73 {
        let mut cut = 73;
        while cut > 0 && !rest.is_char_boundary(cut) {
            cut -= 1;
        }
        out.push(&rest[..cut]);
        rest = &rest[cut..];
    }
    out.push(rest);
    out.join("\r\n ")
}

/// An ISO time in iCalendar's form: an aware one in UTC with a Z, a floating one as it is. "" where it won't read,
/// or is at the very edge of what a time holds, where UTC is past it.
pub fn ics_dt(iso: &str, person: &dyn Zone) -> String {
    let Some(at) = fromisoformat(iso) else { return String::new() };
    if !at.is_aware() {
        return at.basic();
    }
    at.astimezone(&Tz::Fixed(0), person).map(|utc| format!("{}Z", utc.basic())).unwrap_or_default()
}

/// The METHOD:REPLY calendar answering `invite` for one attendee, with `partstat` ACCEPTED, DECLINED or TENTATIVE.
/// It echoes the UID, the ORGANIZER and the DTSTART so strict servers (Google, Exchange) can match it to the event.
pub fn build_reply(
    invite: &Invite,
    attendee_email: &str,
    attendee_name: &str,
    partstat: &str,
    stamp: &Dt,
    person: &dyn Zone,
) -> String {
    let uid = mm_kernel::text::strip(&invite.uid);
    let mut lines: Vec<String> =
        ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//MailMind//iMIP//EN", "CALSCALE:GREGORIAN"]
            .into_iter()
            .chain(["METHOD:REPLY", "BEGIN:VEVENT"])
            .map(str::to_owned)
            .collect();
    if !uid.is_empty() {
        lines.push(fold(&format!("UID:{uid}")));
    }
    lines.push(format!("DTSTAMP:{}", ics_dt(&stamp.isoformat(), person)));
    lines.push("SEQUENCE:0".into());
    if !invite.summary.is_empty() {
        lines.push(fold(&format!("SUMMARY:{}", ics_esc(&invite.summary))));
    }
    if invite.all_day {
        let day: String = invite.start_iso.chars().take(10).filter(|c| *c != '-').collect();
        if day.chars().count() == 8 {
            lines.push(format!("DTSTART;VALUE=DATE:{day}"));
        }
    } else {
        let start = ics_dt(&invite.start_iso, person);
        if !start.is_empty() {
            lines.push(format!("DTSTART:{start}"));
        }
        let end = ics_dt(&invite.end_iso, person);
        if !end.is_empty() {
            lines.push(format!("DTEND:{end}"));
        }
    }
    if let Some(organizer) = invite.organizer.as_ref().filter(|o| !o.email.is_empty()) {
        lines.push(fold(&format!("ORGANIZER{}:mailto:{}", cn(&organizer.name), organizer.email)));
    }
    lines.push(fold(&format!("ATTENDEE{};PARTSTAT={partstat}:mailto:{attendee_email}", cn(attendee_name))));
    lines.extend(["END:VEVENT".to_owned(), "END:VCALENDAR".to_owned()]);
    lines.join("\r\n") + "\r\n"
}

/// What an answer to an invite asks: its message's pk, None where the request names none, and the decision.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Asked {
    pub pk: Option<i64>,
    pub decision: Option<String>,
}

/// `POST /api/invite/rsvp`.
#[derive(Debug)]
pub struct Rsvp {
    stored: Arc<dyn StoredInvites>,
    messages: Arc<dyn Messages>,
    send: Arc<dyn SystemSend>,
    zones: Arc<dyn ZoneNames>,
    clock: Arc<dyn Clock>,
    person: Arc<dyn Zone>,
}

impl Rsvp {
    pub fn new(
        stored: Arc<dyn StoredInvites>,
        messages: Arc<dyn Messages>,
        send: Arc<dyn SystemSend>,
        zones: Arc<dyn ZoneNames>,
        clock: Arc<dyn Clock>,
        person: Arc<dyn Zone>,
    ) -> Rsvp {
        Rsvp { stored, messages, send, zones, clock, person }
    }

    /// The answer, queued in the outbox to the organizer.
    pub fn respond(&self, asked: &Asked) -> Result<Value, Failure> {
        let refused = |words: &str| Failure::Problem(Problem::refused(words));
        let pk = asked.pk.ok_or_else(|| refused("missing message"))?;
        let decision = asked.decision.as_deref().map(|d| mm_kernel::text::strip(d).to_lowercase()).unwrap_or_default();
        let &(decision, partstat, verb) =
            DECISIONS.iter().find(|(d, _, _)| *d == decision).ok_or_else(|| refused("bad decision"))?;
        let row = self.messages.message(pk)?.ok_or_else(|| Failure::Problem(Problem::not_found("not found")))?;
        let me = self.messages.account(&row.account)?.ok_or_else(|| refused("unknown account"))?;
        let raw = self.messages.body(pk)?;
        let here = Here { zones: &*self.zones, person: &*self.person };
        let parts = mm_content::mime::parse(&raw).attachments;
        let (Some(invite), _) = card(&parts, &raw, here) else { return Err(refused("no invite on this message")) };
        let organizer = invite.organizer.as_ref().map(|o| mm_kernel::text::strip(&o.email)).unwrap_or_default();
        if organizer.is_empty() || !organizer.contains('@') {
            return Err(refused("this invite has no organizer to reply to"));
        }
        let me_name = name_for(&invite, &me.email, me.alias.as_deref().filter(|a| !a.is_empty()).unwrap_or(&me.email));
        let stamp = Dt::new(self.clock.now().micros().div_euclid(1_000_000), Tz::Fixed(0));
        let calendar = build_reply(&invite, &me.email, &me_name, partstat, &stamp, here.person);
        let summary = if invite.summary.is_empty() { "(no title)".to_owned() } else { invite.summary.clone() };
        let titled = format!("{}{}", &partstat[..1], partstat[1..].to_lowercase());
        let named = if invite.uid.is_empty() { pk.to_string() } else { invite.uid.clone() };
        let prefix = key_prefix(&row.account, &named);
        let reply = Reply {
            pk,
            account: me.email.clone(),
            account_name: me_name.clone(),
            to: organizer.to_owned(),
            subject: format!("{verb}: {summary}"),
            text: format!(
                "{me_name} has responded \"{titled}\" to the invitation \u{201c}{summary}\u{201d}.\n\n\u{2014} sent from MailMind"
            ),
            calendar,
            idempotency_key: key(&prefix, &self.stored.keys_sent(&prefix)?, partstat),
        };
        let queued = self.send.send(&reply)?;
        Ok(json!({
            "ok": true, "outbox_id": queued.get("outbox_id"), "queued": queued.get("queued"), "decision": decision,
            "organizer": organizer, "summary": summary, "undo_seconds": queued.get("undo_seconds"),
        }))
    }
}
