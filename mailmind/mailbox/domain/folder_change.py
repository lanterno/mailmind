"""A folder a person makes, renames or deletes: the name they typed as IMAP keeps it, and which folders are theirs to change.

A name goes to the server as LIST gives it back, the form the database stores: IMAP's modified UTF-7 for what isn't ASCII
(RFC 3501 5.1.3), with a quoted string's escapes in place. A new folder goes where the account keeps a person's own
(`parent_for_new`), and a rename keeps a folder where it is and changes its last part. The folders that play a part (the Inbox,
Sent, Drafts, Archive, All Mail, Junk and Trash, as `roles.playing` finds them), a folder at the top named as one of them, and
the account's own Trash are never renamed or deleted, nor is a folder the mail server keeps: one with a SPECIAL-USE attribute
(Gmail's Starred and Important among them), or one in Gmail's own tree. A folder with folders inside it isn't deleted. Each
refusal is worded for the person, and none repeats what they typed.
"""
import base64
import re
from collections.abc import Collection

from mailmind.kernel.errors import Conflict, NotFound, Refused
from mailmind.mailbox.domain.roles import Folder, own_name, playing, role_of

NAME_MOST = 200                 # characters in the name a person types

NO_NAME = "A folder needs a name."
TOO_LONG = f"A folder’s name can be up to {NAME_MOST} characters."
UNSENDABLE = "A folder’s name can’t hold a line break or a tab."
NESTED = "A folder’s name can’t hold the server’s separator, {}."
PLAYS_A_PART = "MailMind doesn’t rename or delete the Inbox, Sent, Drafts, Archive, Junk or Trash."
SERVERS_OWN = "The mail server keeps this folder, so MailMind doesn’t rename or delete it."
HAS_FOLDERS = "This folder has folders inside it. Delete or move those first."
TAKEN = "There’s a folder by that name already."
GONE = "That folder isn’t on the server any more."

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# SPECIAL-USE (RFC 6154), and Gmail's \Important: a folder the server fills itself, or a view of mail kept elsewhere.
_SPECIAL_USE = frozenset({"\\all", "\\archive", "\\drafts", "\\flagged", "\\junk", "\\sent", "\\trash", "\\important"})
_SERVERS_TREES = frozenset({"[gmail]", "[google mail]"})


def _shifted(run: str) -> str:
    return "&" + base64.b64encode(run.encode("utf-16-be")).decode("ascii").rstrip("=").replace("/", ",") + "-"


def modified_utf7(text: str) -> str:
    """`text` in IMAP's modified UTF-7: printable ASCII as it is but & as &-, anything else base64 of UTF-16 between & and -,
    with , for /."""
    out: list[str] = []
    run: list[str] = []
    for ch in text:
        if " " <= ch <= "~":
            if run:
                out.append(_shifted("".join(run)))
                run = []
            out.append("&-" if ch == "&" else ch)
        else:
            run.append(ch)
    if run:
        out.append(_shifted("".join(run)))
    return "".join(out)


def as_listed(typed: object, delimiter: str | None) -> str:
    """The name a person typed as LIST would give it back, or Refused saying why it can't be one."""
    name = typed.strip() if isinstance(typed, str) else ""
    if not name:
        raise Refused(NO_NAME)
    if len(name) > NAME_MOST:
        raise Refused(TOO_LONG)
    if _CONTROL.search(name):
        raise Refused(UNSENDABLE)
    if delimiter and delimiter in name:
        raise Refused(NESTED.format(delimiter))
    return modified_utf7(name).replace("\\", "\\\\").replace('"', '\\"')


def delimiter_of(folders: Collection[Folder]) -> str | None:
    """The server's hierarchy separator, as LIST said it: None when it said NIL for every folder."""
    return next((f.delimiter for f in folders if f.delimiter), None)


def named(folders: Collection[Folder], name: str) -> Folder | None:
    """The folder LIST gave by this name; INBOX in any case."""
    inbox = name.upper() == "INBOX"
    return next((f for f in folders if f.name == name or (inbox and f.name.upper() == "INBOX")), None)


def taken(folders: Collection[Folder], name: str) -> bool:
    return named(folders, name) is not None


def _named_for_a_part(folder: Folder) -> bool:
    """A folder at the top whose name or attribute says it plays a part, as a second "Trash" beside the "Bin" that does: never
    a person's own to change either."""
    return folder.depth == 0 and role_of(folder) != "custom"


def _servers_own(folder: Folder) -> bool:
    """A folder the mail server keeps, wherever it sits: one with a SPECIAL-USE attribute, or one in Gmail's own tree. Gmail's
    Starred and Important are views of mail kept elsewhere, and what a delete moved out of one would leave every label."""
    top = folder.name.split(folder.delimiter, 1)[0] if folder.delimiter else folder.name
    return bool(folder.attributes & _SPECIAL_USE) or top.lower() in _SERVERS_TREES


def _why_not(folder: Folder, plays: Collection[str | None], trash: str | None) -> str | None:
    """Why a person may not rename or delete `folder`, in their words, or None when they may."""
    if folder.name.upper() == "INBOX" or folder.name == trash or folder.name in plays or _named_for_a_part(folder):
        return PLAYS_A_PART
    if _servers_own(folder):
        return SERVERS_OWN
    return None


def changeable(folders: Collection[Folder], name: str, trash: str | None) -> Folder:
    """The folder `name` names, when a person may rename or delete it: else NotFound, or Refused for one that plays a part or
    that the server keeps."""
    folder = named(folders, name)
    if folder is None or not folder.selectable:
        raise NotFound(GONE)
    if why := _why_not(folder, playing(folders), trash):
        raise Refused(why)
    return folder


def changeable_names(folders: Collection[Folder], trash: str | None) -> set[str]:
    """The names of the folders a person may rename or delete, as `changeable` says of each: what the rail offers to change."""
    plays = playing(folders)
    return {f.name for f in folders if f.selectable and _why_not(f, plays, trash) is None}


def has_folders_inside(folders: Collection[Folder], folder: Folder) -> bool:
    d = folder.delimiter
    return bool(d) and any(f.name.startswith(folder.name + d) for f in folders)


def renamed_to(folder: Folder, typed: object, folders: Collection[Folder]) -> str:
    """What `folder` is called once renamed to what a person typed: in the same place, its last part changed. The name it has,
    as a person reads it, changes nothing. Conflict when a folder has that name already."""
    d = folder.delimiter
    if isinstance(typed, str) and typed.strip() == own_name(folder.name, d):
        return folder.name
    parent = folder.name.rpartition(d)[0] if d and d in folder.name else ""
    name = (parent + d if parent and d else "") + as_listed(typed, d or delimiter_of(folders))
    if name != folder.name and taken(folders, name):
        raise Conflict(TAKEN)
    return name


def parent_for_new(folders: Collection[Folder]) -> str:
    """Where the account keeps a person's own folders, as its LIST shows, with the separator after it: Proton Bridge's
    Folders, the one place Bridge makes a folder (it lists Folders and Labels, neither selectable); INBOX on a server that keeps
    every folder under it (Courier, and many a host's Dovecot); else nothing, the top."""
    d = delimiter_of(folders)
    if not d:
        return ""
    by_name = {f.name: f for f in folders}
    if all(n in by_name and not by_name[n].selectable for n in ("Folders", "Labels")):
        return "Folders" + d
    others = [f.name for f in folders if f.name.upper() != "INBOX"]
    if others and all(n.upper().startswith("INBOX" + d) for n in others):
        return others[0][:len("INBOX" + d)]
    return ""


def new_name(typed: object, folders: Collection[Folder]) -> str:
    """The name a new folder gets, where the account keeps a person's own (`parent_for_new`): Conflict when a folder has it
    already."""
    name = parent_for_new(folders) + as_listed(typed, delimiter_of(folders))
    if taken(folders, name):
        raise Conflict(TAKEN)
    return name


def moved(stored: str, old: str, new: str, delimiter: str | None) -> str | None:
    """What a stored folder name becomes once `old` is renamed `new`: the folder itself, and on a server with a separator, the
    folders inside it, which go with it. None for any other."""
    if stored == old:
        return new
    if delimiter and stored.startswith(old + delimiter):
        return new + stored[len(old):]
    return None
