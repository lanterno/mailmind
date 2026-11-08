"""What a bulk command may do: its verbs, how far one may reach, what is taken from a model's plan, and the plan a preview
freezes.

Nothing here touches mail. `application.plans` holds the plans open, `application.bulk` makes them, and the mail's own
contexts carry them out. Whether a filter narrows, and whether what it reaches is most of the mailbox, are Discovery's word.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, get_args

from mailmind.kernel.snoozed import SNOOZE_PRESETS, SnoozePreset

TTL = 600            # seconds a preview stays good for
KEPT = 16            # plans held at a time, the newest
# What a command may do, the allowlist: the plan's schema offers the model these and no others, and an answer names one as
# `PlanVerb`, so the page has them as a type.
type PlanVerb = Literal["archive", "trash", "junk", "read", "unread", "star", "snooze"]
VERBS: tuple[PlanVerb, ...] = get_args(PlanVerb.__value__)
CAP = 500            # hard ceiling on how many messages one command may touch
TRASH_CAP = 200      # trash and junk are extra-dangerous, so a lower ceiling
SAMPLE = 8           # how many a preview shows
COMMAND_MAX = 512    # how much of a command is read
FILTER_MAX = 400     # how much of the filter a model wrote is read
APART = frozenset({"sent", "drafts", "trash", "junk"})      # a command reaches these only if its filter names a folder
ALREADY = {"archive": frozenset({"archive", "all"}), "trash": frozenset({"trash"}), "junk": frozenset({"junk"})}     # a verb's own home


@dataclass(frozen=True)
class ActionPlan:
    id: str
    verb: PlanVerb
    preset: SnoozePreset | None
    messages: tuple[tuple[int, str], ...]         # (message key, folder)
    expires: float


def command_of(text: object) -> str:
    return str(text or "").strip()[:COMMAND_MAX]


def filter_told(said: Mapping[str, Any]) -> str:
    return str(said.get("filter") or "").strip()[:FILTER_MAX]


def verb_told(said: Mapping[str, Any]) -> PlanVerb | None:
    """The verb a model's plan acts with, or None when the plan isn't a clean act on the allowlist, which degrades to a
    search: it acts on nothing."""
    verb = said.get("verb")
    return verb if said.get("intent") == "act" and verb in VERBS else None


def preset_told(said: Mapping[str, Any], verb: str) -> SnoozePreset:
    """What a snooze waits for: only a snooze has one to name, and one that names none, or none there is, waits until
    tomorrow, as its preview then says."""
    preset = said.get("snooze_preset") if verb == "snooze" else None
    return preset if preset in SNOOZE_PRESETS else "tomorrow"


def cap_of(verb: str) -> int:
    return TRASH_CAP if verb in ("trash", "junk") else CAP


def out_of_reach(verb: str, names_folder: bool) -> set[str]:
    """The folder roles a command leaves alone. A person who says "archive what Lin sent" means the mail that is around,
    not the copy already archived, one in Trash or their own in Sent, so a filter reaches those only by naming a folder,
    and a verb never reaches its own home."""
    return set(ALREADY.get(verb, frozenset())) | (set() if names_folder else set(APART))
