//! An answer to an invite (`Rsvp`) and the invite a message carries (`invites::card`), on fakes behind the
//! calendar's ports: what goes to the organizer, under which key, and each way a request is refused before anything
//! is sent (tests/unit/calendar/test_rsvp_use_case.py and test_rsvp_reply.py). All fictional.

#![allow(clippy::unwrap_used)]

mod common;

use std::sync::{Arc, Mutex};

use common::*;
use mm_calendar::invite::Party;
use mm_calendar::invites::card;
use mm_calendar::ports::{Me, Message, Messages, Reply, SystemSend};
use mm_calendar::rsvp::{self, Asked, Rsvp};
use mm_calendar::time::{Dt, Tz};
use mm_content::mime::Attachment;
use mm_kernel::errors::{Failure, Fault, Kind, Problem};
use mm_kernel::zone::Utc;
use serde_json::{Value, json};

const ADA: &str = "ada@example.test";
const ICS: [&str; 11] = [
    "BEGIN:VCALENDAR",
    "METHOD:REQUEST",
    "BEGIN:VEVENT",
    "UID:standup-1",
    "SUMMARY:Standup",
    "DTSTART:20261103T100000Z",
    "ORGANIZER;CN=Ren:mailto:ren@example.test",
    "ATTENDEE;CN=\"Ada L.\":mailto:ada@example.test",
    "END:VEVENT",
    "END:VCALENDAR",
    "",
];

/// 2026-11-02 12:00 UTC.
const STAMP: i64 = 1_793_620_800;

/// A message holding `ics` as its second part, as a calendar sends one.
fn raw_invite(ics: &str, content_type: &str, filename: Option<&str>) -> Vec<u8> {
    let disposition = filename.map_or_else(|| "attachment".to_owned(), |f| format!("attachment; filename=\"{f}\""));
    [
        "From: ren@example.test",
        &format!("To: {ADA}"),
        "Subject: Invitation: Standup",
        "MIME-Version: 1.0",
        "Content-Type: multipart/mixed; boundary=\"b\"",
        "",
        "--b",
        "Content-Type: text/plain; charset=utf-8",
        "",
        "You're invited.",
        "--b",
        &format!("Content-Type: {content_type}; charset=utf-8"),
        &format!("Content-Disposition: {disposition}"),
        "",
        ics,
        "--b--",
        "",
    ]
    .join("\r\n")
    .into_bytes()
}

fn the_invite() -> Vec<u8> {
    raw_invite(&ICS.join("\r\n"), "text/calendar", None)
}

/// The message, its account, its body and the outbox, each as a test leaves them.
#[derive(Debug)]
struct World {
    raw: Mutex<Vec<u8>>,
    accounts: Mutex<Vec<Me>>,
    fetch_fails: Mutex<bool>,
    send_refuses: Mutex<Option<String>>,
    queued: Mutex<Vec<Reply>>,
}

impl World {
    fn new() -> Arc<World> {
        World::with(the_invite())
    }

    fn with(raw: Vec<u8>) -> Arc<World> {
        Arc::new(World {
            raw: Mutex::new(raw),
            accounts: Mutex::new(vec![Me { email: ADA.into(), alias: Some("Ada".into()) }]),
            fetch_fails: Mutex::new(false),
            send_refuses: Mutex::new(None),
            queued: Mutex::new(Vec::new()),
        })
    }

    fn queued(&self) -> Vec<Reply> {
        self.queued.lock().unwrap().clone()
    }
}

impl Messages for World {
    fn message(&self, pk: i64) -> Result<Option<Message>, Fault> {
        Ok((pk == 7).then(|| Message { pk: 7, key: Some(41), account: ADA.into() }))
    }

    fn account(&self, email: &str) -> Result<Option<Me>, Fault> {
        Ok(self.accounts.lock().unwrap().iter().find(|a| a.email == email).cloned())
    }

    fn body(&self, _pk: i64) -> Result<Vec<u8>, Failure> {
        if *self.fetch_fails.lock().unwrap() {
            return Err(Failure::Problem(Problem::new(Kind::Unreachable, "could not load the invite")));
        }
        Ok(self.raw.lock().unwrap().clone())
    }
}

impl SystemSend for World {
    fn send(&self, reply: &Reply) -> Result<Value, Failure> {
        if let Some(words) = self.send_refuses.lock().unwrap().clone() {
            return Err(Failure::Problem(Problem::refused(words)));
        }
        self.queued.lock().unwrap().push(reply.clone());
        Ok(json!({"outbox_id": 41, "queued": true, "undo_seconds": 10}))
    }
}

fn rsvp_in(world: &Arc<World>, stored: Arc<Stored>, pk: Option<i64>, decision: Option<&str>) -> Result<Value, Failure> {
    Rsvp::new(stored, world.clone(), world.clone(), Arc::new(Zones), Arc::new(Frozen(STAMP)), Arc::new(Utc))
        .respond(&Asked { pk, decision: decision.map(str::to_owned) })
}

fn answer(world: &Arc<World>, decision: &str) -> Result<Value, Failure> {
    rsvp_in(world, Arc::new(Stored::default()), Some(7), Some(decision))
}

fn refusal(failed: Result<Value, Failure>) -> (Kind, String) {
    match failed {
        Err(Failure::Problem(problem)) => (problem.kind, problem.said),
        other => panic!("not refused: {other:?}"),
    }
}

// ── the invite a message carries ────────────────────────────────────────────

fn part(part_id: u32, content_type: &str, filename: &str) -> Attachment {
    Attachment {
        part_id,
        filename: filename.into(),
        content_type: content_type.into(),
        size: 0,
        content_id: None,
        is_inline: false,
        travels: true,
    }
}

#[test]
fn a_calendar_part_is_read_into_the_invite_and_its_part() {
    let by_type = (the_invite(), part(2, "text/calendar", ""));
    let by_name = (
        raw_invite(&ICS.join("\r\n"), "application/octet-stream", Some("INVITE.ICS")),
        part(2, "application/octet-stream", "INVITE.ICS"),
    );
    for (raw, calendar) in [by_type, by_name] {
        let parts = [part(1, "text/plain", ""), calendar];
        let (invite, part_id) = card(&parts, &raw, here(&Utc));
        let invite = invite.unwrap();
        assert_eq!((invite.summary.as_str(), invite.uid.as_str(), part_id), ("Standup", "standup-1", Some(2)));
    }
}

#[test]
fn a_message_with_no_invite_to_read_has_none() {
    let calendar = part(2, "text/calendar", "");
    let none: [(&[Attachment], Vec<u8>); 3] = [
        (&[], the_invite()),
        (std::slice::from_ref(&calendar), Vec::new()),
        (&[part(9, "text/calendar", "")], the_invite()),
    ];
    for (parts, raw) in none {
        assert_eq!(card(parts, &raw, here(&Utc)), (None, None), "{parts:?}");
    }
}

// ── an answer ───────────────────────────────────────────────────────────────

#[test]
fn an_answer_queues_a_reply_to_the_organizer_under_its_own_key() {
    let world = World::new();
    let out = answer(&world, " Accept ").unwrap();
    assert_eq!(
        out,
        json!({"ok": true, "outbox_id": 41, "queued": true, "decision": "accept", "organizer": "ren@example.test",
               "summary": "Standup", "undo_seconds": 10})
    );
    let [reply] = world.queued().try_into().unwrap();
    assert_eq!(
        (reply.idempotency_key.as_str(), reply.subject.as_str(), reply.to.as_str(), reply.pk),
        ("rsvp:ada@example.test:standup-1:ACCEPTED:0", "Accepted: Standup", "ren@example.test", 7)
    );
    assert!(
        reply.calendar.contains("METHOD:REPLY") && reply.calendar.contains("PARTSTAT=ACCEPTED:mailto:ada@example.test")
    );
    assert_eq!(reply.account_name, "Ada L.");
    assert!(reply.text.starts_with("Ada L. has responded \"Accepted\""), "{}", reply.text);
}

#[test]
fn the_reply_is_the_one_the_python_app_wrote() {
    let world = World::new();
    answer(&world, "accept").unwrap();
    assert_eq!(
        world.queued()[0].calendar,
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//MailMind//iMIP//EN",
            "CALSCALE:GREGORIAN",
            "METHOD:REPLY",
            "BEGIN:VEVENT",
            "UID:standup-1",
            "DTSTAMP:20261102T120000Z",
            "SEQUENCE:0",
            "SUMMARY:Standup",
            "DTSTART:20261103T100000Z",
            "ORGANIZER;CN=\"Ren\":mailto:ren@example.test",
            "ATTENDEE;CN=\"Ada L.\";PARTSTAT=ACCEPTED:mailto:ada@example.test",
            "END:VEVENT",
            "END:VCALENDAR",
            "",
        ]
        .join("\r\n")
    );
}

#[test]
fn the_same_answer_again_is_the_same_send_and_a_changed_mind_a_new_one() {
    let world = World::new();
    let stored = Arc::new(Stored::default());
    stored.sent.lock().unwrap().push("rsvp:ada@example.test:standup-1:DECLINED:0".into());
    rsvp_in(&world, stored.clone(), Some(7), Some("decline")).unwrap();
    rsvp_in(&world, stored, Some(7), Some("tentative")).unwrap();
    let keys: Vec<String> = world.queued().into_iter().map(|r| r.idempotency_key).collect();
    assert_eq!(keys, ["rsvp:ada@example.test:standup-1:DECLINED:0", "rsvp:ada@example.test:standup-1:TENTATIVE:1"]);
}

#[test]
fn a_request_that_names_no_answer_or_no_message_is_refused() {
    let asked = [
        (None, Some("accept"), Kind::Refused, "missing message"),
        (Some(7), Some("maybe"), Kind::Refused, "bad decision"),
        (Some(7), None, Kind::Refused, "bad decision"),
        (Some(8), Some("accept"), Kind::NotFound, "not found"),
    ];
    for (pk, decision, kind, said) in asked {
        let world = World::new();
        assert_eq!(refusal(rsvp_in(&world, Arc::new(Stored::default()), pk, decision)), (kind, said.to_owned()));
        assert!(world.queued().is_empty());
    }
}

#[test]
fn a_message_of_an_account_gone_is_refused() {
    let world = World::new();
    world.accounts.lock().unwrap().clear();
    assert_eq!(refusal(answer(&world, "accept")), (Kind::Refused, "unknown account".to_owned()));
}

#[test]
fn a_message_that_cant_be_fetched_says_so() {
    let world = World::new();
    *world.fetch_fails.lock().unwrap() = true;
    assert_eq!(refusal(answer(&world, "accept")), (Kind::Unreachable, "could not load the invite".to_owned()));
}

#[test]
fn a_message_with_no_invite_is_refused() {
    let world = World::with(raw_invite("not a calendar", "text/plain", None));
    assert_eq!(refusal(answer(&world, "accept")), (Kind::Refused, "no invite on this message".to_owned()));
}

#[test]
fn an_invite_with_no_organizer_to_reply_to_is_refused() {
    for organizer in [None, Some("ORGANIZER;CN=Ren:mailto:ren")] {
        let mut lines: Vec<&str> = ICS.iter().copied().filter(|l| !l.starts_with("ORGANIZER")).collect();
        if let Some(organizer) = organizer {
            lines.insert(3, organizer);
        }
        let world = World::with(raw_invite(&lines.join("\r\n"), "text/calendar", None));
        assert_eq!(
            refusal(answer(&world, "accept")),
            (Kind::Refused, "this invite has no organizer to reply to".to_owned()),
            "{organizer:?}"
        );
        assert!(world.queued().is_empty());
    }
}

#[test]
fn a_reply_the_outbox_wont_take_is_refused_in_its_own_words() {
    let world = World::new();
    *world.send_refuses.lock().unwrap() = Some("the account has no address to send from".into());
    assert_eq!(
        refusal(answer(&world, "accept")),
        (Kind::Refused, "the account has no address to send from".to_owned())
    );
    assert!(world.queued().is_empty());
}

// ── what the reply says ─────────────────────────────────────────────────────

fn invite_of(lines: &[&str]) -> mm_calendar::invite::Invite {
    let mut all = vec!["BEGIN:VCALENDAR", "METHOD:REQUEST", "BEGIN:VEVENT", "UID:u", "DTSTART:20261102T100000"];
    all.extend(lines);
    all.extend(["END:VEVENT", "END:VCALENDAR", ""]);
    parse(&all.join("\r\n")).unwrap()
}

fn line_of(reply: &str, starts: &str) -> String {
    reply.replace("\r\n ", "").split("\r\n").find(|l| l.starts_with(starts)).unwrap().to_owned()
}

fn stamp() -> Dt {
    Dt::new(STAMP, Tz::Fixed(0))
}

#[test]
fn a_quote_in_a_name_doesnt_end_the_parameter_it_goes_in() {
    let mut inv = invite_of(&["ATTENDEE;CN=\"Ada ^'Lovelace^' Park\":mailto:ada@example.test"]);
    inv.organizer = Some(Party { name: "Ren\"; ROLE=CHAIR; X=\"".into(), email: "ren@example.test".into() });
    let reply = rsvp::build_reply(&inv, ADA, "Ada \"Lovelace\" Park", "ACCEPTED", &stamp(), &Utc);
    assert_eq!(line_of(&reply, "ORGANIZER"), "ORGANIZER;CN=\"Ren^'\\; ROLE=CHAIR\\; X=^'\":mailto:ren@example.test");
    assert_eq!(
        line_of(&reply, "ATTENDEE"),
        "ATTENDEE;CN=\"Ada ^'Lovelace^' Park\";PARTSTAT=ACCEPTED:mailto:ada@example.test"
    );
}

#[test]
fn a_name_with_no_quote_goes_as_it_did() {
    let inv = invite_of(&["ORGANIZER;CN=\"Takeda, Ren\":mailto:ren@example.test"]);
    let reply = rsvp::build_reply(&inv, ADA, "Ada", "DECLINED", &stamp(), &Utc);
    assert_eq!(line_of(&reply, "ORGANIZER"), "ORGANIZER;CN=\"Takeda\\, Ren\":mailto:ren@example.test");
}

#[test]
fn a_time_utc_would_take_past_a_datetime_leaves_the_reply_without_it() {
    for start in ["DTSTART;TZID=Asia/Tokyo:00010101T000000", "DTSTART;TZID=America/New_York:99991231T235959"] {
        let text = ["BEGIN:VCALENDAR", "BEGIN:VEVENT", "UID:u", start, "END:VEVENT", "END:VCALENDAR", ""].join("\r\n");
        let reply = rsvp::build_reply(&parse(&text).unwrap(), ADA, "Ada", "ACCEPTED", &stamp(), &Utc);
        assert!(reply.contains("UID:u") && !reply.contains("DTSTART"), "{start}");
    }
}
