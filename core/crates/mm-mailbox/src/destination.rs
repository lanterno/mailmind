//! Where a filing verb sends a message on an account's server, and which way it goes there
//! (`mailbox.domain.destination` and `move_strategy`). Values in and values out: `find` asks the server which folder
//! plays a role, and only a verb that needs to know calls it; `server_ops` carries the way out.
//!
//! There are three ways. Gmail's archive goes nowhere: the message stays in All Mail, and only its \Inbox label goes
//! (`Way::Labels`). A server that offers MOVE gets a UID MOVE (`Way::Move`), which goes by copy if it is refused; one
//! that doesn't gets a COPY, checked, then \Deleted and a UID EXPUNGE of each UID alone (`Way::Copy`). No way sends
//! a bulk EXPUNGE, on any server, so Proton Bridge, which breaks on one, takes the same ways as any server offering
//! the same. Bridge marks a message read as it moves to Trash, so a move there reads \Seen first, which is how an
//! Undo knows to take the mark off.

/// What an account's provider does that a move must know (`accounts.domain.provider`'s profile).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Provider {
    /// Archive drops \Inbox; the message stays in All Mail (Gmail).
    pub archive_is_label_removal: bool,
    /// A move to Trash sets \Seen (Proton).
    pub marks_trash_read: bool,
    /// The Spam folder when LIST shows none.
    pub junk_fallback: Option<String>,
}

impl Provider {
    /// The profile of an account of `kind`.
    pub fn of_kind(kind: &str) -> Provider {
        match kind {
            "gmail" => Provider {
                archive_is_label_removal: true,
                junk_fallback: Some("[Gmail]/Spam".to_owned()),
                ..Provider::default()
            },
            "proton" => Provider { marks_trash_read: true, ..Provider::default() },
            _ => Provider::default(),
        }
    }
}

/// Where a message goes.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Destination {
    /// None, with no label to drop: the account has nowhere to put it.
    pub folder: Option<String>,
    pub drop_inbox_label: bool,
}

impl Destination {
    pub fn folder(folder: Option<String>) -> Destination {
        Destination { folder, drop_inbox_label: false }
    }
}

/// The Destination of a `kind` of filing. `named` is where the command's row already says it goes: a folder a
/// person picked, for a `move`. `find` asks which folder plays the first of these roles that one plays.
pub fn resolve<E>(
    kind: &str,
    named: Option<&str>,
    provider: &Provider,
    trash: Option<&str>,
    find: impl FnOnce(&[&str]) -> Result<Option<String>, E>,
) -> Result<Destination, E> {
    if kind == "archive" && provider.archive_is_label_removal {
        return Ok(Destination { folder: None, drop_inbox_label: true });
    }
    if let Some(named) = named.filter(|n| !n.is_empty()) {
        return Ok(Destination::folder(Some(named.to_owned())));
    }
    Ok(Destination::folder(match kind {
        "trash" => trash.map(str::to_owned),
        "junk" => find(&["junk"])?.or_else(|| provider.junk_fallback.clone()),
        "archive" => find(&["archive", "all"])?,
        _ => None,
    }))
}

/// As much of `resolve` as holds with no server to ask, as a command is accepted and its Undo written. A folder only
/// the server can name is left for a try to find, so no folder here doesn't yet mean nowhere to go.
pub fn known_without_server(kind: &str, archive_is_label_removal: bool, trash: Option<&str>) -> Destination {
    let provider = Provider { archive_is_label_removal, ..Provider::default() };
    resolve::<()>(kind, None, &provider, trash, |_| Ok(None)).unwrap_or_default()
}

/// Which way a message moves.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Way {
    Labels,
    Move,
    Copy,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct MoveStrategy {
    pub way: Way,
    /// The server sets \Seen as it lands: a reversal needs to know what it was.
    pub read_seen_first: bool,
}

/// Which way a `kind` of filing takes to `place`, on a server with these capabilities. `trash` is the account's
/// Trash folder, where a server may set the read mark whichever verb sent the message.
pub fn choose(
    kind: &str,
    place: &Destination,
    capabilities: &[String],
    marks_trash_read: bool,
    trash: Option<&str>,
) -> MoveStrategy {
    if place.drop_inbox_label {
        return MoveStrategy { way: Way::Labels, read_seen_first: false };
    }
    MoveStrategy {
        way: if crate::session::offers_move(capabilities) { Way::Move } else { Way::Copy },
        read_seen_first: marks_trash_read
            && (kind == "trash" || (place.folder.is_some() && place.folder.as_deref() == trash)),
    }
}
