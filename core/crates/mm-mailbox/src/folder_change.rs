//! A folder a person makes, renames or deletes (`mailmind.mailbox.domain.folder_change`): the name they typed as IMAP
//! keeps it, and which folders are theirs to change.
//!
//! A name goes to the server as LIST gives it back, the form the database stores: IMAP's modified UTF-7 for what isn't
//! ASCII (RFC 3501 5.1.3), with a quoted string's escapes in place. A new folder goes where the account keeps a
//! person's own (`parent_for_new`), and a rename keeps a folder where it is and changes its last part. The folders
//! that play a part (the Inbox, Sent, Drafts, Archive, All Mail, Junk and Trash, as `roles::playing` finds them), a
//! folder at the top named as one of them, and the account's own Trash are never renamed or deleted, nor is a folder
//! the mail server keeps: one with a SPECIAL-USE attribute (Gmail's Starred and Important among them), or one in
//! Gmail's own tree. A folder with folders inside it isn't deleted. Each refusal is worded for the person, and none
//! repeats what they typed.

use std::collections::BTreeSet;

use mm_kernel::errors::{Kind, Problem};

use crate::roles::{Folder, own_name, playing, role_of};

/// Characters in the name a person types.
pub const NAME_MOST: usize = 200;

pub const NO_NAME: &str = "A folder needs a name.";
pub const TOO_LONG: &str = "A folder’s name can be up to 200 characters.";
pub const UNSENDABLE: &str = "A folder’s name can’t hold a line break or a tab.";
pub const PLAYS_A_PART: &str = "MailMind doesn’t rename or delete the Inbox, Sent, Drafts, Archive, Junk or Trash.";
pub const SERVERS_OWN: &str = "The mail server keeps this folder, so MailMind doesn’t rename or delete it.";
pub const HAS_FOLDERS: &str = "This folder has folders inside it. Delete or move those first.";
pub const TAKEN: &str = "There’s a folder by that name already.";
pub const GONE: &str = "That folder isn’t on the server any more.";

/// What a person is told of a name holding the server's separator.
pub fn nested(delimiter: &str) -> String {
    format!("A folder’s name can’t hold the server’s separator, {delimiter}.")
}

/// SPECIAL-USE (RFC 6154), and Gmail's Important, as attributes in lower case: a folder the server fills itself, or a
/// view of mail kept elsewhere.
const SPECIAL_USE: [&str; 8] =
    ["\\all", "\\archive", "\\drafts", "\\flagged", "\\junk", "\\sent", "\\trash", "\\important"];
/// The trees Gmail keeps its own folders in.
const SERVERS_TREES: [&str; 2] = ["[gmail]", "[google mail]"];

const BASE64: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+,";

/// A run of what isn't printable ASCII, shifted: `&`, its UTF-16 in base64 with `,` for `/` and no padding, `-`.
fn shifted(run: &[u16], out: &mut String) {
    let bytes: Vec<u8> = run.iter().flat_map(|unit| unit.to_be_bytes()).collect();
    out.push('&');
    for chunk in bytes.chunks(3) {
        let n = chunk.iter().enumerate().fold(0u32, |n, (i, b)| n | (u32::from(*b) << (16 - 8 * i)));
        for i in 0..=chunk.len() {
            out.push(char::from(BASE64[((n >> (18 - 6 * i)) & 63) as usize]));
        }
    }
    out.push('-');
}

/// `text` in IMAP's modified UTF-7: printable ASCII as it is but `&` as `&-`, anything else shifted.
pub fn modified_utf7(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut run: Vec<u16> = Vec::new();
    for ch in text.chars() {
        if (' '..='~').contains(&ch) {
            if !run.is_empty() {
                shifted(&run, &mut out);
                run.clear();
            }
            if ch == '&' {
                out.push_str("&-");
            } else {
                out.push(ch);
            }
        } else {
            let mut units = [0u16; 2];
            run.extend_from_slice(ch.encode_utf16(&mut units));
        }
    }
    if !run.is_empty() {
        shifted(&run, &mut out);
    }
    out
}

/// The name a person typed as LIST would give it back, or the refusal saying why it can't be one.
pub fn as_listed(typed: Option<&str>, delimiter: Option<&str>) -> Result<String, Problem> {
    let name = typed.unwrap_or_default().trim();
    if name.is_empty() {
        return Err(Problem::refused(NO_NAME));
    }
    if name.chars().count() > NAME_MOST {
        return Err(Problem::refused(TOO_LONG));
    }
    if name.chars().any(|c| c.is_ascii_control()) {
        return Err(Problem::refused(UNSENDABLE));
    }
    if let Some(d) = delimiter.filter(|d| !d.is_empty() && name.contains(*d)) {
        return Err(Problem::refused(nested(d)));
    }
    Ok(modified_utf7(name).replace('\\', "\\\\").replace('"', "\\\""))
}

/// The server's hierarchy separator, as LIST said it: None when it said NIL for every folder.
pub fn delimiter_of(folders: &[Folder]) -> Option<&str> {
    folders.iter().find_map(|f| f.delimiter.as_deref().filter(|d| !d.is_empty()))
}

/// The folder LIST gave by this name; INBOX in any case.
pub fn named<'a>(folders: &'a [Folder], name: &str) -> Option<&'a Folder> {
    let inbox = name.eq_ignore_ascii_case("INBOX");
    folders.iter().find(|f| f.name == name || (inbox && f.name.eq_ignore_ascii_case("INBOX")))
}

fn taken(folders: &[Folder], name: &str) -> bool {
    named(folders, name).is_some()
}

fn conflict(said: &str) -> Problem {
    Problem::new(Kind::Conflict, said)
}

/// A folder at the top whose name or attribute says it plays a part, as a second "Trash" beside the "Bin" that does:
/// never a person's own to change either.
fn named_for_a_part(folder: &Folder) -> bool {
    folder.depth() == 0 && role_of(folder) != "custom"
}

/// A folder the mail server keeps, wherever it sits: one with a SPECIAL-USE attribute, or one in Gmail's own tree.
/// Gmail's Starred and Important are views of mail kept elsewhere, and what a delete moved out of one would leave
/// every label.
fn servers_own(folder: &Folder) -> bool {
    let top = match folder.delimiter.as_deref().filter(|d| !d.is_empty()) {
        Some(d) => folder.name.split(d).next().unwrap_or_default(),
        None => folder.name.as_str(),
    };
    SPECIAL_USE.iter().any(|a| folder.attributes.contains(*a)) || SERVERS_TREES.contains(&top.to_lowercase().as_str())
}

/// Why a person may not rename or delete `folder`, in their words, or None when they may.
fn why_not(folder: &Folder, plays: &[(String, &'static str)], trash: Option<&str>) -> Option<&'static str> {
    let name = folder.name.as_str();
    if name.eq_ignore_ascii_case("INBOX")
        || Some(name) == trash
        || plays.iter().any(|(n, _)| n == name)
        || named_for_a_part(folder)
    {
        return Some(PLAYS_A_PART);
    }
    servers_own(folder).then_some(SERVERS_OWN)
}

/// The folder `name` names, when a person may rename or delete it: else not found, or refused for one that plays a
/// part or that the server keeps.
pub fn changeable<'a>(folders: &'a [Folder], name: &str, trash: Option<&str>) -> Result<&'a Folder, Problem> {
    let folder = named(folders, name).filter(|f| f.selectable()).ok_or_else(|| Problem::not_found(GONE))?;
    match why_not(folder, &playing(folders), trash) {
        Some(why) => Err(Problem::refused(why)),
        None => Ok(folder),
    }
}

/// The names of the folders a person may rename or delete, as `changeable` says of each: what the rail offers to
/// change.
pub fn changeable_names(folders: &[Folder], trash: Option<&str>) -> BTreeSet<String> {
    let plays = playing(folders);
    folders.iter().filter(|f| f.selectable() && why_not(f, &plays, trash).is_none()).map(|f| f.name.clone()).collect()
}

pub fn has_folders_inside(folders: &[Folder], folder: &Folder) -> bool {
    match folder.delimiter.as_deref().filter(|d| !d.is_empty()) {
        Some(d) => {
            let inside = format!("{}{d}", folder.name);
            folders.iter().any(|f| f.name.starts_with(&inside))
        }
        None => false,
    }
}

/// What `folder` is called once renamed to what a person typed: in the same place, its last part changed. The name it
/// has, as a person reads it, changes nothing. A conflict when a folder has that name already.
pub fn renamed_to(folder: &Folder, typed: Option<&str>, folders: &[Folder]) -> Result<String, Problem> {
    let own = folder.delimiter.as_deref().filter(|d| !d.is_empty());
    if typed.is_some_and(|t| t.trim() == own_name(&folder.name, own)) {
        return Ok(folder.name.clone());
    }
    let parent = own.and_then(|d| folder.name.rsplit_once(d)).map(|(parent, _)| parent).filter(|p| !p.is_empty());
    let last = as_listed(typed, own.or_else(|| delimiter_of(folders)))?;
    let name = match (parent, own) {
        (Some(parent), Some(d)) => format!("{parent}{d}{last}"),
        _ => last,
    };
    if name != folder.name && taken(folders, &name) {
        return Err(conflict(TAKEN));
    }
    Ok(name)
}

/// Where the account keeps a person's own folders, as its LIST shows, with the separator after it: Proton Bridge's
/// Folders, the one place Bridge makes a folder (it lists Folders and Labels, neither selectable); INBOX on a server
/// that keeps every folder under it (Courier, and many a host's Dovecot); else nothing, the top.
pub fn parent_for_new(folders: &[Folder]) -> String {
    let Some(d) = delimiter_of(folders) else { return String::new() };
    let unselectable = |name: &str| folders.iter().any(|f| f.name == name && !f.selectable());
    if unselectable("Folders") && unselectable("Labels") {
        return format!("Folders{d}");
    }
    let inbox = format!("INBOX{d}");
    let under_inbox = |name: &&str| name.get(..inbox.len()).is_some_and(|head| head.eq_ignore_ascii_case(&inbox));
    let others: Vec<&str> =
        folders.iter().map(|f| f.name.as_str()).filter(|n| !n.eq_ignore_ascii_case("INBOX")).collect();
    match others.first() {
        Some(first) if others.iter().all(under_inbox) => first[..inbox.len()].to_owned(),
        _ => String::new(),
    }
}

/// The name a new folder gets, where the account keeps a person's own (`parent_for_new`): a conflict when a folder
/// has it already.
pub fn new_name(typed: Option<&str>, folders: &[Folder]) -> Result<String, Problem> {
    let name = parent_for_new(folders) + &as_listed(typed, delimiter_of(folders))?;
    if taken(folders, &name) {
        return Err(conflict(TAKEN));
    }
    Ok(name)
}

/// What a stored folder name becomes once `old` is renamed `new`: the folder itself, and on a server with a separator,
/// the folders inside it, which go with it. None for any other.
pub fn moved(stored: &str, old: &str, new: &str, delimiter: Option<&str>) -> Option<String> {
    if stored == old {
        return Some(new.to_owned());
    }
    let d = delimiter.filter(|d| !d.is_empty())?;
    stored.strip_prefix(old).filter(|rest| rest.starts_with(d)).map(|rest| format!("{new}{rest}"))
}
