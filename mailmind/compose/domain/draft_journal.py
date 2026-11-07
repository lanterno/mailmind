"""What a draft still asks of its server (decision D5): a save is local, and a push follows it on close, on an explicit save
and at most every PUSH_EVERY seconds while someone types. Its one row holds that save's bytes and what the composer held at
it, or that the copy is to go."""
import hashlib
import json
from typing import Any, TypedDict, TypeGuard

from mailmind.compose.domain.builder import Built, Composed
from mailmind.compose.domain.resume import lf
from mailmind.content.headers import addr_chip
from mailmind.kernel import addresses
from mailmind.kernel.row import Row

PUSH_EVERY = 60
PUSH_TICK = 10               # the push job looks at least this often
BACKOFF_FIRST = 20
BACKOFF_MOST = 900
WAITING_AFTER = 30           # the tray names a save this overdue


class Record(TypedDict):
    """What a save records of what the composer held. It describes these very bytes and no others."""
    raw_sha: str
    own_text: str
    tail_text: str | None
    tail_html: str | None
    bcc: str | None


def sha_of(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def record_of(spec: Composed, built: Built) -> Record:
    """Bcc is written nowhere in the bytes, so this is all that keeps it. `spec` has been built, so its addresses parse."""
    bcc = [addr_chip(n, a) for n, a in addresses.parse_addr_list(spec["bcc"])]
    return {"raw_sha": sha_of(built["raw"]), "own_text": lf(spec["own"]),
            "tail_text": lf(spec["tail"]["text"]) if spec["tail"]["text"] else None, "tail_html": spec["tail"]["html"],
            "bcc": json.dumps(bcc) if bcc else None}


def due_after_typing(now: int, pushed_at: int | None) -> int:
    """When an autosave's copy may go: PUSH_EVERY after the last push, or after this first save."""
    return max(now, pushed_at + PUSH_EVERY) if pushed_at else now + PUSH_EVERY


def due_after_failing(now: int, attempts: int) -> int:
    """A push that failed goes again later and later. The bytes wait here, so nothing is ever given up."""
    return now + min(BACKOFF_MOST, BACKOFF_FIRST * 2 ** min(attempts, 10))


def after_save(autosave: bool, now: int, waiting: Row | None, pushed_at: int | None) -> tuple[int, int]:
    """(when the push is due, the tries it has had), for a save replacing what `waiting` asked. An autosave changes neither,
    so typing never puts a push off or forgets that one failed. Any other save goes now, afresh."""
    if not autosave:
        return now, 0
    if waiting:
        return waiting["due_at"], waiting["attempts"]
    return due_after_typing(now, pushed_at), 0


def asks_removal(j: Row | None) -> bool:
    """All it asks is that the copy goes: the draft is as good as gone, so a later save is a draft of its own."""
    return j is not None and bool(j["discard"])


def holds_a_save(j: Row | None) -> TypeGuard[Row]:
    return j is not None and bool(j["raw"])


def copy_may_be_there(row: Row, j: Row | None) -> bool:
    """Is there a copy for a discard to wait on: the one the row tracks, or one a try may have left?"""
    return bool(row["uid"] or (j is not None and j["tried"]))


def left_by_a_try(j: Row | None, account: str) -> str | None:
    """The Message-ID of a copy a try at this save may have left in `account`, which a delivered send takes with it."""
    return j["tried"] if j is not None and j["tried"] and j["account"] == account else None


def next_round_in(now: int, due: int | None) -> int:
    """Seconds until the push job looks again, with `due` the next save to come due. One due already is on a busy gate, so
    it gets no sooner look than the next tick."""
    return PUSH_TICK if due is None or due <= now else min(PUSH_TICK, due - now)


def overdue_since(now: int, pushing: bool) -> int:
    """Saves from before this should have reached their server. With no push job running (read-only), every one should."""
    return now - (WAITING_AFTER if pushing else 0)


def listed(r: Row) -> dict[str, Any]:
    """A save the tray names."""
    return {"id": r["draft_id"], "account": r["account"], "subject": r["subject"] or "", "saved_at": r["saved_at"],
            "attempts": r["attempts"], "discarding": bool(r["discard"])}
