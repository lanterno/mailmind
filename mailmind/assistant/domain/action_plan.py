"""What a bulk command may do: its verbs, how far one may reach, and the plan a preview freezes.

Nothing here touches mail. `application.plans` holds the plans open, `application.bulk` makes them, and the mail's own
contexts carry them out.
"""
from dataclasses import dataclass

TTL = 600            # seconds a preview stays good for
KEPT = 16            # plans held at a time, the newest
VERBS = frozenset({"archive", "trash", "junk", "read", "unread", "star", "snooze"})
CAP = 500            # hard ceiling on how many messages one command may touch
TRASH_CAP = 200      # trash and junk are extra-dangerous, so a lower ceiling
SAMPLE = 8           # how many a preview shows
APART = frozenset({"sent", "drafts", "trash", "junk"})      # a command reaches these only if its filter names a folder
ALREADY = {"archive": frozenset({"archive", "all"}), "trash": frozenset({"trash"}), "junk": frozenset({"junk"})}     # a verb's own home


@dataclass(frozen=True)
class ActionPlan:
    id: str
    verb: str
    preset: str | None
    messages: tuple[tuple[int, str], ...]         # (message key, folder)
    expires: float


def cap_of(verb: str) -> int:
    return TRASH_CAP if verb in ("trash", "junk") else CAP


def out_of_reach(verb: str, names_folder: bool) -> set[str]:
    """The folder roles a command leaves alone. A person who says "archive what Lin sent" means the mail that is around,
    not the copy already archived, one in Trash or their own in Sent, so a filter reaches those only by naming a folder,
    and a verb never reaches its own home."""
    return set(ALREADY.get(verb, frozenset())) | (set() if names_folder else set(APART))
