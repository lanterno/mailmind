//! Which folder plays which part on an account's server (`mailmind.mailbox.domain.roles`).
//!
//! A folder's role is one of `ROLES`, or "custom". It comes, in order, from its name when that is INBOX, which IMAP
//! reserves; its SPECIAL-USE attribute (RFC 6154); then its name, the last segment of its path, matched whole
//! against the names servers and mail clients give each role. Where several folders could play one role, an
//! attribute beats a name, a shallower path a deeper one, and then the name, in code point order.

use std::collections::BTreeSet;
use std::sync::LazyLock;

use mm_kernel::text;
use regex::Regex;

pub const ROLES: [&str; 7] = ["inbox", "sent", "drafts", "archive", "all", "junk", "trash"];

/// SPECIAL-USE attributes, in lower case, and the roles they give; in this order where a folder has several.
const ATTRIBUTES: [(&str, &str); 6] = [
    ("\\sent", "sent"),
    ("\\drafts", "drafts"),
    ("\\archive", "archive"),
    ("\\all", "all"),
    ("\\junk", "junk"),
    ("\\trash", "trash"),
];

const SUFFIX: &str = r"(?:[ _-]?(?:mail|e-?mail|messages?|items?|folder))?";

static NAMES: LazyLock<Vec<(&'static str, Regex)>> = LazyLock::new(|| {
    [
        ("sent", format!("sent{SUFFIX}")),
        ("drafts", "drafts?".to_owned()),
        ("archive", "archives?".to_owned()),
        ("all", "all[ _-]?mail".to_owned()),
        ("junk", format!("(?:junk|spam|bulk){SUFFIX}")),
        ("trash", format!("(?:trash|bin|deleted){SUFFIX}")),
    ]
    .into_iter()
    .map(|(role, source)| (role, Regex::new(&format!("^(?:{source})$")).expect("a role's pattern compiles")))
    .collect()
});

/// A folder as LIST gave it: its name in LIST's form (escapes in place), its attributes in lower case, and the
/// server's hierarchy delimiter, None when LIST said NIL.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Folder {
    pub name: String,
    pub attributes: BTreeSet<String>,
    pub delimiter: Option<String>,
}

impl Folder {
    pub fn named(name: &str) -> Folder {
        Folder { name: name.to_owned(), attributes: BTreeSet::new(), delimiter: None }
    }

    pub fn selectable(&self) -> bool {
        !(self.attributes.contains("\\noselect") || self.attributes.contains("\\nonexistent"))
    }

    /// How deep the folder sits: by the server's delimiter, or with none, by "/" and "." as `role_by_name` reads it.
    pub fn depth(&self) -> usize {
        match self.delimiter.as_deref() {
            Some(d) if !d.is_empty() => self.name.matches(d).count(),
            _ => self.name.chars().filter(|c| matches!(c, '/' | '.')).count(),
        }
    }
}

fn last_segment(name: &str, delimiter: Option<&str>) -> String {
    let name = name.replace("\\\"", "\"").replace("\\\\", "\\");
    let last = match delimiter {
        Some(d) if !d.is_empty() => name.rsplit(d).next().unwrap_or(""),
        _ => name.rsplit(['/', '.']).next().unwrap_or(""),
    };
    text::strip(last).to_lowercase()
}

/// The role a folder's name alone gives it. With no delimiter, both "/" and "." separate the path.
pub fn role_by_name(name: &str, delimiter: Option<&str>) -> &'static str {
    if name.to_uppercase() == "INBOX" {
        return "inbox";
    }
    let segment = last_segment(name, delimiter);
    NAMES.iter().find(|(_, pattern)| pattern.is_match(&segment)).map_or("custom", |(role, _)| role)
}

fn by_attribute(folder: &Folder) -> Option<&'static str> {
    ATTRIBUTES.iter().find(|(attribute, _)| folder.attributes.contains(*attribute)).map(|(_, role)| *role)
}

/// The role `folder` plays: INBOX by name, then its attribute, then its name.
pub fn role_of(folder: &Folder) -> &'static str {
    if folder.name.to_uppercase() == "INBOX" {
        return "inbox";
    }
    by_attribute(folder).unwrap_or_else(|| role_by_name(&folder.name, folder.delimiter.as_deref()))
}

/// The name of the selectable folder playing the first of `roles` that one plays, or None.
pub fn find<'a>(folders: &'a [Folder], roles: &[&str]) -> Option<&'a str> {
    for role in roles {
        let found = folders
            .iter()
            .filter(|f| f.selectable() && role_of(f) == *role)
            .map(|f| (usize::from(!(*role == "inbox" || by_attribute(f) == Some(*role))), f.depth(), f.name.as_str()))
            .min();
        if let Some((_, _, name)) = found {
            return Some(name);
        }
    }
    None
}

/// The role each folder `find` gives one plays: one folder a role, so a second with that name is `custom`.
pub fn playing(folders: &[Folder]) -> Vec<(String, &'static str)> {
    ROLES.iter().filter_map(|role| find(folders, &[role]).map(|name| (name.to_owned(), *role))).collect()
}

fn plays(playing: &[(String, &'static str)], name: &str) -> &'static str {
    playing.iter().rev().find(|(n, _)| n == name).map_or("custom", |(_, role)| role)
}

/// A folder on the rail: its name, its role, and its unread and total counts.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RailFolder {
    pub name: String,
    pub role: &'static str,
    pub unseen: i64,
    pub total: i64,
}

fn rail_place(role: &str) -> u8 {
    match role {
        "inbox" => 0,
        "sent" => 1,
        "drafts" => 2,
        "archive" => 3,
        "all" => 4,
        "junk" => 8,
        "trash" => 9,
        _ => 5,
    }
}

/// The rail's order: the folders that play a part, in theirs, with a person's own between them, by name.
pub fn rail_order(mut folders: Vec<RailFolder>) -> Vec<RailFolder> {
    folders.sort_by_cached_key(|f| (rail_place(f.role), f.name.to_lowercase()));
    folders
}

/// The rail of folders a live LIST gave, with each one's counts from `status`.
pub fn live_rail(folders: &[Folder], mut status: impl FnMut(&str) -> (i64, i64)) -> Vec<RailFolder> {
    let selectable: Vec<Folder> = folders.iter().filter(|f| f.selectable()).cloned().collect();
    let playing = playing(&selectable);
    let rail = selectable
        .iter()
        .map(|f| {
            let (unseen, total) = status(&f.name);
            RailFolder { name: f.name.clone(), role: plays(&playing, &f.name), unseen, total }
        })
        .collect();
    rail_order(rail)
}

/// The rail from what is stored alone, with no server to ask, from (folder, unseen, total). INBOX is always on it.
/// Roles go by name, as a live list's do; a stored name carries no delimiter, so "/" and "." both mark its depth.
pub fn stored_rail(counts: &[(String, Option<i64>, i64)]) -> Vec<RailFolder> {
    let mut held: Vec<(String, Option<i64>, i64)> = counts.to_vec();
    if !held.iter().any(|(name, _, _)| name == "INBOX") {
        held.push(("INBOX".to_owned(), Some(0), 0));
    }
    held.sort_by_cached_key(|(name, _, _)| {
        (name.chars().filter(|c| matches!(c, '/' | '.')).count(), name.to_lowercase())
    });
    let folders: Vec<Folder> = held.iter().map(|(name, _, _)| Folder::named(name)).collect();
    let playing = playing(&folders);
    rail_order(
        held.into_iter()
            .map(|(name, unseen, total)| RailFolder {
                role: plays(&playing, &name),
                name,
                unseen: unseen.unwrap_or(0),
                total,
            })
            .collect(),
    )
}

/// The parts a folder may play and a person still not file mail into (`destination.CLOSED_TO_FILING`).
pub fn takes_filing(role: &str) -> bool {
    !matches!(role, "drafts" | "sent" | "all")
}
