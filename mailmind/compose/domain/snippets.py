"""Snippets: your own words kept under a short name, which `;name` in the composer's text brings in. What makes one, and
what is read back of those saved. {{A placeholder}} in the text is only words here: going through them is the page's."""
import re
from collections.abc import Iterable
from typing import Literal, TypedDict

from mailmind.compose.domain.resume import lf
from mailmind.kernel.errors import Refused
from mailmind.kernel.parsed import is_list, is_object

type SnippetVerb = Literal["save", "delete"]           # what a request may do to the snippets: its schema names it so

MAX_SNIPPETS = 200
MAX_TEXT = 20_000
_TRIGGER = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")


class Snippet(TypedDict):
    trigger: str                                       # what names it, once, with no `;` and in lowercase
    text: str


def trigger_of(raw: object) -> str | None:
    """The trigger this names, however it was typed, or None for what can't be one."""
    if not isinstance(raw, str):
        return None
    trigger = raw.strip().removeprefix(";").lower()
    return trigger if _TRIGGER.fullmatch(trigger) else None


def _in_order(items: Iterable[Snippet]) -> list[Snippet]:
    return sorted(items, key=lambda s: s["trigger"])


def clean(raw: object) -> list[Snippet]:
    """The snippets out of whatever was saved. TOTAL: never raises, drops what isn't one, and of two with a trigger keeps
    the first."""
    found: dict[str, Snippet] = {}
    for item in raw if is_list(raw) else []:
        if not is_object(item):
            continue
        trigger, text = trigger_of(item.get("trigger")), item.get("text")
        if trigger and isinstance(text, str) and text.strip():
            found.setdefault(trigger, {"trigger": trigger, "text": lf(text)[:MAX_TEXT]})
    return _in_order(found.values())[:MAX_SNIPPETS]


def saved(current: Iterable[Snippet], trigger: object, text: str | None) -> list[Snippet]:
    """The snippets with this one added, or put in the place of the one its trigger names. Refused for what can't be kept,
    and never cut short: those are someone's words."""
    name = trigger_of(trigger)
    if not name:
        raise Refused("a trigger is up to 32 letters, digits, - or _")
    words = lf(text).strip("\n")
    if not words.strip():
        raise Refused("a snippet needs some text")
    if len(words) > MAX_TEXT:
        raise Refused(f"a snippet can be up to {MAX_TEXT:,} characters")
    others = [s for s in current if s["trigger"] != name]
    if len(others) >= MAX_SNIPPETS:
        raise Refused(f"there can be {MAX_SNIPPETS} snippets: delete one first")
    return _in_order([*others, {"trigger": name, "text": words}])


def without(current: Iterable[Snippet], trigger: object) -> list[Snippet] | None:
    """The snippets with this one gone, or None if that changes nothing."""
    name, before = trigger_of(trigger), list(current)
    kept = [s for s in before if s["trigger"] != name]
    return kept if len(kept) != len(before) else None
