"""A draft's rules, which the Drafts and the Outbox use cases go by: what makes a save, whether one may go in, what counts as a
draft and which copy is ours to remove. Values in and values out. Where a fact comes from a port it is asked for through
the callable given, and only if the rule gets that far."""
import base64
from collections.abc import Callable, Mapping
from typing import Any, TypedDict

from mailmind.compose.domain.builder import Composed
from mailmind.compose.domain.resume import ResumedFile, lf
from mailmind.kernel.ids import AccountId, FolderName
from mailmind.kernel.row import Row

HELD = "This draft is in the outbox, waiting to be sent. Cancel it there first to change it."
DISCARDED = "You discarded this draft. It leaves the server as soon as the server can be reached."
CHANGED_ELSEWHERE = "This draft changed in another window."


class InPlace(TypedDict):
    """The copy a push asks its own APPEND to replace, as the Mailbox's `append_to_drafts` names it."""
    prev_uid: int | None
    prev_uidvalidity: int | None
    prev_msgid: str | None
    prev_folder: str | None


class HandedOver(TypedDict):
    """What a first save tells the composer, so it needs no original from here on."""
    resume: dict[str, str]
    files: list[ResumedFile]


def composer_key(body: Mapping[str, Any]) -> str | None:
    """The composer's idempotency key, as a save or a send gives it."""
    return str(body.get("idempotency_key") or "").strip()[:200] or None


def is_empty(spec: Composed) -> bool:
    """Nothing in it to keep. It is never saved, so the Drafts folder doesn't fill with empty messages."""
    return not (spec["subject"].strip() or (spec["text"] or "").strip()
                or spec["to"] or spec["cc"] or spec["bcc"] or spec["attachments"])


def is_stale(prev: Row, base_version: object, key: str | None) -> bool:
    """A save names the version it saw. One that never saw the latest may not overwrite it, unless the last save was this
    composer's, whose answer was lost. One that names none overwrites: someone asked for that."""
    return base_version is not None and base_version != prev["version"] and (key is None or prev["saved_by"] != key)


def next_version(prev: Row | None) -> int:
    return prev["version"] + 1 if prev else 1


def is_draft(row: Row, plays_drafts: Callable[[AccountId, FolderName], bool]) -> bool:
    """A message is a draft if the server flags it \\Draft, or it sits in the account's Drafts: never by what a folder is
    called."""
    return bool(row["draft"]) or plays_drafts(row["account"], row["folder"])


def copy_is_ours(d: Row, names_drafts: Callable[[str], bool], flagged: Callable[[], bool]) -> bool:
    """May this `drafts` row's copy be removed from the server? A removal has no Undo, so only one MailMind saved (the row
    holds its hash, and a save that never synced has no `messages` row to be flagged), one the server flags \\Draft, or one
    in a folder named Drafts. Received mail filed under "Contract drafts" stays, and a `drafts` row may well point at it:
    Edit was once offered in any folder whose name held "draft"."""
    if not d["uid"]:                         # a save still only in the journal: nothing to remove
        return False
    return bool(d["raw_sha"]) or names_drafts(d["folder"]) or flagged()


def spans_accounts(row: Row, waiting: Row) -> bool:
    """An old copy in another account (From changed) is removed there too, so doing what the journal asks spans both."""
    return row["account"] != waiting["account"] and bool(row["uid"])


def replaced_in_place(prev: Row, j: Row) -> InPlace:
    """The copy this push's APPEND replaces in its own mailbox. From changed, and the UID is another mailbox's: it names
    nothing there."""
    same = prev["account"] == j["account"]
    return {"prev_uid": prev["uid"] if same else None, "prev_uidvalidity": prev["uidvalidity"] if same else None,
            "prev_msgid": prev["message_id"] if same else None, "prev_folder": prev["folder"] if same else None}


def left_elsewhere(prev: Row, j: Row, folder: str) -> bool:
    """Is the copy this push replaced somewhere its APPEND couldn't reach it, now the new copy is safe in `folder`? In another
    mailbox (From changed) or another folder (a draft reopened from elsewhere). Without a UID and its epoch to vouch for it,
    no."""
    moved = prev["account"] != j["account"] or (prev["folder"] and prev["folder"] != folder)
    return bool(prev["uid"] and prev["uidvalidity"] is not None and moved)


def handed_over(spec: Composed) -> HandedOver:
    """From here on the composer names the quote, the threading and the files itself, as a reopened draft's does, so its
    saves and its send no longer need the original's row."""
    return {"resume": {"in_reply_to": spec["in_reply_to"] or "", "references": " ".join(spec["references"] or []),
                       "tail_text": lf(spec["tail"]["text"])},
            "files": [{"filename": a["filename"], "content_type": a["content_type"], "size": len(a["data"]),
                       "b64": base64.b64encode(a["data"]).decode("ascii")} for a in spec["forwarded"]]}


def tracked(account: str, message_id: str | None, subject: str | None, mode: str, at: str, *, folder: str | None = None,
            uid: int | None = None, uidvalidity: int | None = None, in_reply_to_pk: int | None = None) -> dict[str, Any]:
    """A new `drafts` row. Saved here first it has no copy yet: only another app's draft, found on the server, says where."""
    return {"id": None, "account": account, "folder": folder, "uid": uid, "message_id": message_id, "uidvalidity": uidvalidity,
            "in_reply_to_pk": in_reply_to_pk, "mode": mode, "subject": subject, "created_at": at, "updated_at": at}
