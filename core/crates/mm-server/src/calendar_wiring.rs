//! The calendar bound to its adapters (`bootstrap.calendar`): Today & next over the invites stored mail holds, an
//! answer to an invite, an event file of a person's own, and the invite a message opens with. Each reads its times in
//! the person's zone, a TZID in the system's zone database.
//!
//! An answer goes out through Compose's outbox, which isn't on this server yet: it says so once everything an answer
//! is checked for has passed, so a request it would refuse is refused as the Python app refuses it.

use std::sync::Arc;

use mm_accounts::Directory;
use mm_calendar::agenda::Agenda;
use mm_calendar::ports::{FolderRoles, Me, Message, Messages, Reply, SystemSend, ZoneNames};
use mm_calendar::publish::Publishing;
use mm_calendar::rsvp::Rsvp;
use mm_kernel::errors::{Failure, Fault, Kind, NOT_YET, Problem};
use mm_kernel::log::{self, Field};
use mm_kernel::seams::Clock;
use mm_kernel::zone::Zone;
use mm_mailbox::ports::BodyFetch;
use mm_store::Database;
use mm_store::calendar::SystemZones;
use serde_json::Value;

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

/// A message MailMind sends for itself goes through the outbox, which isn't on this server yet.
#[derive(Debug)]
pub(crate) struct OutboxNotYet;

impl SystemSend for OutboxNotYet {
    fn send(&self, _reply: &Reply) -> Result<Value, Failure> {
        Err(Failure::Fault(NOT_YET))
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
}

impl Calendar {
    pub(crate) fn assemble(parts: CalendarParts) -> Calendar {
        let CalendarParts { database, directory, fetch, zones, clock, zone } = parts;
        let zones: Arc<dyn ZoneNames> = zones;
        let messages = Arc::new(RsvpMessages { database: database.clone(), directory, fetch });
        Calendar {
            agenda: Agenda::new(database.clone(), Arc::new(NamedRoles), zones.clone(), clock.clone(), zone.clone()),
            rsvp: Rsvp::new(database.clone(), messages, Arc::new(OutboxNotYet), zones, clock.clone(), zone.clone()),
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
