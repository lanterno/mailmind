"""A folder a person makes, renames or deletes, on the server through the one command path (MailCommands.change_folder), and
then here. Nothing of it queues: a server that can't be reached says so, and nothing has changed.

A folder that plays a part, that the server keeps, or that holds folders isn't deleted (`domain.folder_change`). One that holds
mail is deleted only once a person has said so, having been told how much it holds, and then its mail goes to the account's
Trash first, as the server lists it (`roles.trash`): a MOVE of each message by UID, or a copy then each original's own UID
EXPUNGE, never a bulk EXPUNGE. If any mail is still there after that, it isn't deleted. On Gmail a folder is a label, and its
mail stays in All Mail and under every other label it has: so nothing moves, and the label alone goes, as Gmail's own delete
does. A delete that stops once mail has gone to Trash says so. A renamed folder's rows go by its new name here, and a deleted
one's go, its mail stored again as Trash is next read.
"""
import imaplib
from collections.abc import Callable
from typing import Any

from mailmind.kernel.errors import Conflict, NotFound, Problem, Refused, Unreachable
from mailmind.mailbox.application.commands import MailCommands
from mailmind.mailbox.application.folders import Folders
from mailmind.mailbox.application.ports import Accounts, StoredMail
from mailmind.mailbox.application.server_ops import UNREACHABLE
from mailmind.mailbox.application.session import MailSession
from mailmind.mailbox.domain import folder_change, roles
from mailmind.platform import profile
from mailmind.platform.db.uow import UnitOfWork

SERVER_SAID_NO = "The server didn’t make that change."
NOT_REACHED = "The server couldn’t be reached, so nothing changed. Try again in a moment."
NO_ACCOUNT = "unknown account"
STILL_HOLDS = "Mail is still in this folder, so it wasn’t deleted. Try again."
MAY_BE_IN_TRASH = "The server stopped answering, so some of this folder’s mail may be in Trash already. Try again in a moment."
MOVE_BATCH = 200            # UIDs to one MOVE into Trash, so no command line grows too long


def _messages(count: int) -> str:
    return "1 message" if count == 1 else f"{count} messages"


def holds(count: int, labels: bool = False) -> str:
    """What a person is told of a folder that holds mail, before they say to delete it: on Gmail (`labels`), that the mail
    stays."""
    if labels:
        return f"This folder holds {_messages(count)}. Deleting it leaves them in All Mail."
    return f"This folder holds {_messages(count)}. Deleting it moves them to Trash first."


def kept_folder(moved: int) -> str:
    """What a person is told when mail went to Trash and then the folder stayed."""
    return f"Moved {_messages(moved)} to Trash, but the server kept the folder."


class FolderChanges:
    def __init__(self, uow: UnitOfWork, store: StoredMail, accounts: Accounts, commands: MailCommands, folders: Folders):
        self._uow = uow
        self._store = store
        self._accounts = accounts
        self._commands = commands
        self._folders = folders

    def _trash(self, account: str) -> tuple[str, bool]:
        """The Trash the account names, and whether its folders are Gmail's labels."""
        try:
            acct = self._accounts.get(account)
        except KeyError:
            raise NotFound(NO_ACCOUNT) from None
        return self._accounts.trash_folder(acct), self._accounts.profile(acct).archive_is_label_removal

    def _on_server[T](self, account: str, change: Callable[[MailSession], T]) -> T:
        """change(session) as a job on the account's gate, each failure in a person's words."""
        try:
            return self._commands.change_folder(account, change)
        except (Problem, profile.ReadOnlyError):
            raise
        except KeyError:
            raise NotFound(NO_ACCOUNT) from None
        except UNREACHABLE:
            raise Unreachable(NOT_REACHED) from None
        except imaplib.IMAP4.error:
            raise Conflict(SERVER_SAID_NO) from None

    def create(self, account: str | None, name: str | None) -> dict[str, Any]:
        """A new folder at the top of the account's, by the name a person typed."""
        if not account:
            raise Refused("account required")

        def make(s: MailSession) -> str:
            made = folder_change.new_name(name, s.folders())
            s.create(made)
            return made
        made = self._on_server(account, make)
        self._folders.invalidate(account)
        return {"ok": True, "folder": made}

    def rename(self, account: str | None, folder: str | None, name: str | None) -> dict[str, Any]:
        """`folder` called what a person typed, where it is, with the folders inside it."""
        if not account or not folder:
            raise Refused("account and folder required")
        trash, _ = self._trash(account)

        def rename(s: MailSession) -> tuple[str, str | None]:
            folders = s.folders()
            found = folder_change.changeable(folders, folder, trash)
            to = folder_change.renamed_to(found, name, folders)
            if to != folder:
                s.rename(folder, to)
            return to, found.delimiter
        to, delimiter = self._on_server(account, rename)
        if to != folder:
            with self._uow.write() as c:
                moving = {stored: new for stored in self._store.stored_folder_names(c, account)
                          if (new := folder_change.moved(stored, folder, to, delimiter)) is not None}
                self._store.rename_folders(c, account, moving)
        self._folders.invalidate(account)
        return {"ok": True, "folder": to}

    def delete(self, account: str | None, folder: str | None, confirmed: bool) -> dict[str, Any]:
        """Delete `folder`: at once when it holds no mail, and when it does, once a person has said so (`confirmed`), after
        its mail has gone to Trash, or on Gmail with its mail left in All Mail. Conflict, saying how much it holds and whether
        it goes to Trash, until they have. One that stops once mail has gone says so, and how much went."""
        if not account or not folder:
            raise Refused("account and folder required")
        named, labels = self._trash(account)
        trash, moved, moving = named, 0, False

        def delete(s: MailSession) -> int:
            nonlocal trash, moved, moving
            folders = s.folders()
            found = folder_change.changeable(folders, folder, named)
            trash = roles.trash(folders, named)  # as the server lists it: a localised or renamed Trash too
            if folder_change.has_folders_inside(folders, found):
                raise Refused(folder_change.HAS_FOLDERS)
            s.select(folder, readonly=False)
            held = s.uids("UNDELETED")
            if held and not confirmed:
                raise Conflict(holds(len(held), labels), messages=len(held), to_trash=not labels)
            going = [] if labels else held       # on Gmail the mail stays in All Mail, and the label alone goes
            for start in range(0, len(going), MOVE_BATCH):
                batch = going[start:start + MOVE_BATCH]
                moving = True
                s.move(batch, trash)
                moved += len(batch)
            if going and s.uids("UNDELETED"):
                raise Conflict(STILL_HOLDS)
            s.select("INBOX")                    # leave it, expunging nothing, before it goes
            s.delete(folder)
            return moved
        try:
            trashed = self._on_server(account, delete)
        except (Conflict, Unreachable) as e:
            if isinstance(e, Unreachable) and moving:
                self._deleted(account, folder, trash, moved=True)
                raise Unreachable(MAY_BE_IN_TRASH) from None
            if isinstance(e, Conflict) and moved:
                self._deleted(account, folder, trash, moved=True)
                raise Conflict(kept_folder(moved), trashed=moved) from None
            raise
        self._deleted(account, folder, trash, moved=trashed > 0)
        return {"ok": True, "trashed": trashed}

    def _deleted(self, account: str, folder: str, trash: str, moved: bool) -> None:
        """The folder's rows go, so what is still in it is read again when it next opens, and Trash, which mail went to, is
        caught up as it opens."""
        with self._uow.write() as c:
            self._store.forget_folder(c, account, folder)
        self._folders.invalidate(account)
        if moved:
            self._folders.touched(account, trash)
