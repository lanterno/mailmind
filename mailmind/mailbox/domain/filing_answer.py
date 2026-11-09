"""What a person hears of their filing: the words for each verb, what a server's refusal says, and how many moves at once come
to one answer."""
from collections.abc import Mapping, Sequence
from typing import Any

from mailmind.kernel.errors import NotFound, Problem
from mailmind.kernel.ids import FolderName, MessageKey
from mailmind.mailbox.domain import command

GONE = "not found"
FILED = {"trash": "trashed", "archive": "archived", "junk": "junked", "move": "moved"}
_REFUSED = {"archive": "the server refused the archive"}
_NO_FOLDER = {"archive": "no archive folder on this account", "junk": "no junk folder on this account"}


def refusal(kind: str, outcome: str) -> str:
    """What a person is told of a move of `kind` that won't happen: the verb's own words, else the plain ones."""
    return (_NO_FOLDER.get(kind) if outcome == command.NO_FOLDER else None) or _REFUSED.get(kind, "the server refused the move")


def of_many(accepted: Sequence[tuple[int, command.Accepted | None]], results: Mapping[int, Mapping[str, Any] | Problem],
            names: Mapping[int, tuple[MessageKey, FolderName, int]]) -> dict[str, Any]:
    """What the person hears of many moves: one the server refused went back alone, and the rest stay filed, named by their
    key, folder and uid as they were. With none filed, what the first raised, or NotFound when there was none to file."""
    filed = {p: said for p, _ in accepted if not isinstance(said := results[p], Problem)}
    if not filed:
        raise next((no for p, _ in accepted if isinstance(no := results[p], Problem)), NotFound(GONE))
    queued = sum(said.get("status") == "queued" for said in filed.values())
    return {"ok": True, "msg": "queued" if queued else "filed",
            "filed": [{"key": names[p][0], "folder": names[p][1], "uid": names[p][2]} for p in filed if p in names],
            "count": len(filed), "failed": len(accepted) - len(filed),
            "queued": queued, "undoable": any(said.get("undoable") for said in filed.values())}


def rows_tally(results: Mapping[int, Mapping[str, Any] | Problem], members: Sequence[Sequence[int]]) -> dict[str, Any]:
    """How marked rows came out, each standing for `members` of its own: a row failed if none of its messages was filed, a
    copy no longer there among them, and is partial if only some were."""
    each = [sum(not isinstance(results[p], Problem) for p in pks) for pks in members]
    return {"rows_failed": each.count(0), "partial": any(0 < n < len(pks) for n, pks in zip(each, members))}
