//! The calendar bound to its adapters (`bootstrap.calendar`): Today & next over the invites stored mail holds, an
//! answer to an invite, an event file of a person's own, and the invite a message opens with. Each reads its times in
//! the person's zone, a TZID in the system's zone database.
//!
//! An answer goes out through Compose's outbox, built by Compose's builder as a reply to the invite.

use std::sync::Arc;

use mm_accounts::Directory;
use mm_calendar::agenda::Agenda;
use mm_calendar::ports::{FolderRoles, Me, Message, Messages, Reply, SystemSend, ZoneNames};
use mm_calendar::publish::Publishing;
use mm_calendar::rsvp::Rsvp;
use mm_compose::builder::reference_chain;
use mm_compose::delivery::Delivery;
use mm_compose::sending::{Mint, Outgoing};
use mm_compose::spec::{Attachment, MessageSpec};
use mm_kernel::errors::{Failure, Fault, Kind, NOT_YET, Problem};
use mm_kernel::log::{self, Field};
use mm_kernel::seams::Clock;
use mm_kernel::zone::Zone;
use mm_mailbox::ports::BodyFetch;
use mm_store::Database;
use mm_store::calendar::SystemZones;
use serde_json::{Value, json};

use crate::compose_wiring::RandomDraws;

const TAG: &str = "rsvp";

/// The part a folder plays, as its name alone gives it (the Mailbox's roles).
#[derive(Debug)]
pub(crate) struct NamedRoles;

impl FolderRoles for NamedRoles {
    fn role_by_name(&self, folder: &str) -> String {
        mm_mailbox::roles::role_by_name(folder, None).to_owned()
    }
}

/// What an answer needs of a message and its account: the stored row, the Directory's account, and the body as the
/// Mailbox fetches it.
#[derive(Debug)]
pub(crate) struct RsvpMessages {
    pub(crate) database: Arc<Database>,
    pub(crate) directory: Arc<Directory>,
    pub(crate) fetch: Arc<dyn BodyFetch>,
}

impl Messages for RsvpMessages {
    fn message(&self, pk: i64) -> Result<Option<Message>, Fault> {
        self.database.read(|c| mm_store::calendar::message(c, pk))
    }

    fn account(&self, email: &str) -> Result<Option<Me>, Fault> {
        Ok(self.directory.get(email)?.map(|a| Me { email: a.email, alias: a.alias }))
    }

    /// A body that can't be had is said in words that name no one; the log names the row and the fault's class.
    fn body(&self, pk: i64) -> Result<Vec<u8>, Failure> {
        match self.fetch.fetch(pk) {
            Ok(raw) => Ok(raw.unwrap_or_default()),
            Err(NOT_YET) => Err(Failure::Fault(NOT_YET)),
            Err(fault) => {
                let id = pk.to_string();
                log::warning(
                    TAG,
                    "fetch failed for pk %(id)s: %(why)s",
                    &[(Field::Id, &id), (Field::Why, fault.class)],
                    None,
                );
                Err(Failure::Problem(Problem::new(Kind::Unreachable, "could not load the invite")))
            }
        }
    }
}

/// An answer to an invite, built and queued in Compose's outbox as the Python app's `_Send` does: a reply to the
/// message it answers, its calendar a METHOD:REPLY part named `invite.ics`.
#[derive(Debug)]
pub(crate) struct ComposeSend {
    pub(crate) outgoing: Outgoing,
    pub(crate) database: Arc<Database>,
}

impl SystemSend for ComposeSend {
    fn send(&self, reply: &Reply) -> Result<Value, Failure> {
        let [message_id, refs, in_reply_to] =
            self.database.read(|c| mm_store::calendar::threading(c, reply.pk))?.unwrap_or_default();
        let spec = MessageSpec {
            account: reply.account.clone(),
            account_name: reply.account_name.clone(),
            to: vec![reply.to.clone()],
            subject: reply.subject.clone(),
            text: reply.text.clone(),
            attachments: vec![Attachment {
                filename: "invite.ics".to_owned(),
                content_type: "text/calendar".to_owned(),
                data: reply.calendar.clone().into_bytes(),
                params: vec![("method".to_owned(), "REPLY".to_owned()), ("charset".to_owned(), "UTF-8".to_owned())],
                cid: None,
            }],
            references: reference_chain(message_id.as_deref(), refs.as_deref(), in_reply_to.as_deref()),
            in_reply_to: message_id,
            ..MessageSpec::default()
        };
        // The builder's own words, which hold nothing secret.
        let built = self.outgoing.build(&spec).map_err(Problem::refused)?;
        let (id, queued) =
            self.outgoing.send(&built, &reply.idempotency_key, &reply.subject, Some(reply.pk), &reply.account)?;
        Ok(json!({"outbox_id": id, "queued": queued, "undo_seconds": self.outgoing.send_delay()}))
    }
}

/// The calendar's use cases, bound.
#[derive(Debug)]
pub struct Calendar {
    pub agenda: Agenda,
    pub rsvp: Rsvp,
    pub publishing: Publishing,
    /// The database, for the message a request names.
    database: Arc<Database>,
}

/// What `Calendar` is made of.
pub(crate) struct CalendarParts {
    pub(crate) database: Arc<Database>,
    pub(crate) directory: Arc<Directory>,
    /// The Mailbox's body fetch, as a message opened has it.
    pub(crate) fetch: Arc<dyn BodyFetch>,
    pub(crate) zones: Arc<SystemZones>,
    pub(crate) clock: Arc<dyn Clock>,
    /// The person's zone.
    pub(crate) zone: Arc<dyn Zone>,
    /// Compose's outbox, which an answer is queued in.
    pub(crate) delivery: Arc<Delivery>,
}

impl Calendar {
    pub(crate) fn assemble(parts: CalendarParts) -> Calendar {
        let CalendarParts { database, directory, fetch, zones, clock, zone, delivery } = parts;
        let zones: Arc<dyn ZoneNames> = zones;
        let messages = Arc::new(RsvpMessages { database: database.clone(), directory, fetch });
        // Minted as Compose mints a send.
        let mint = Mint { clock: clock.clone(), draws: Arc::new(RandomDraws), pid: std::process::id() };
        let send = Arc::new(ComposeSend {
            outgoing: Outgoing::new(database.clone(), delivery, mint),
            database: database.clone(),
        });
        Calendar {
            agenda: Agenda::new(database.clone(), Arc::new(NamedRoles), zones.clone(), clock.clone(), zone.clone()),
            rsvp: Rsvp::new(database.clone(), messages, send, zones, clock.clone(), zone.clone()),
            publishing: Publishing::new(database.clone(), clock, zone),
            database,
        }
    }

    /// A copy of message `key` now: the one at `uid` in `folder`, else in `folder`, else another; None where none is
    /// left.
    pub(crate) fn message_pk(&self, key: i64, folder: Option<&str>, uid: Option<i64>) -> Result<Option<i64>, Fault> {
        self.database.read(|c| mm_store::copies::message_pk(c, key, folder, uid))
    }
}
