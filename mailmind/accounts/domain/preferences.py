"""What a person has set for how MailMind looks and behaves: the "preferences" block of mailmind.config.json, beside the accounts.

A name the block doesn't hold, or holds something that can't be that preference (a hand edit), is that preference's default, so a
file with no block goes on exactly as before and reading one writes nothing. A change names only what changes and leaves the rest
of the file as it stands. The AI's daily limit is one more setting, but the file keeps it at the top level, as it always did
("ai_daily_limit_usd"), where the Assistant reads it.

Each closed set is a `type`, so a request's schema takes it by name and the page has it as a type and keeps no list of its own.
"""
import math
import re
from collections.abc import Mapping
from typing import Any, Literal, get_args

from mailmind.kernel import send_window
from mailmind.kernel.draft_tone import DEFAULT_TONE, DraftTone
from mailmind.kernel.errors import Refused
from mailmind.kernel.parsed import is_object

type Density = Literal["comfortable", "cozy", "compact"]
type Theme = Literal["day", "dusk", "night"]
type ReadWidth = Literal["narrow", "medium", "wide"]
type ReadSize = Literal["s", "m", "l", "xl"]
type ReadFont = Literal["sans", "serif", "mono"]
type MailColors = Literal["auto", "dark", "original"]
type SearchScope = Literal["all", "account"]

CHOICES: Mapping[str, tuple[str, ...]] = {
    "density": get_args(Density.__value__),
    "theme": get_args(Theme.__value__),
    "read_width": get_args(ReadWidth.__value__),
    "read_size": get_args(ReadSize.__value__),
    "read_font": get_args(ReadFont.__value__),
    "mail_colors": get_args(MailColors.__value__),
    "search_scope": get_args(SearchScope.__value__),
    "draft_tone": get_args(DraftTone.__value__),
}
# Every preference the block may hold, and what it is with nothing said. What the page's stores start from (`just gen-api`).
DEFAULTS: Mapping[str, Any] = {
    "undo_send_seconds": send_window.DEFAULT,
    "density": "comfortable",
    "high_contrast": False,
    "theme": "day",
    "read_width": "medium",
    "read_size": "m",
    "read_font": "sans",
    "mail_colors": "auto",
    "single_keys": True,
    "conversations": True,
    "search_scope": "all",
    "draft_tone": DEFAULT_TONE,
    "notify_new_mail": True,
    "notify_meetings": True,
    "quiet_hours": False,
    "quiet_from": "22:00",
    "quiet_until": "07:00",
    "guide_offered": False,                 # the first-run guide has been offered, here and in any browser from now on
    "morning_brief": False,
    "weekly_review": False,                 # the week's review written ahead on Monday morning, and read all week
    "auto_tidy": False,
}
AI_DAILY_LIMIT = "ai_daily_limit_usd"       # at the file's top level, not in the block
AI_DAILY_LIMIT_MAX = 1000                   # dollars: past this is a slip of the keyboard, not a budget
BLOCK = "preferences"

_TIMES = ("quiet_from", "quiet_until")
_CLOCK = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")
_REFUSALS = {"undo_send_seconds": f"the Undo Send window must be {send_window.SHORTEST} to {send_window.LONGEST} seconds",
             AI_DAILY_LIMIT: f"the AI daily limit must be more than $0 and at most ${AI_DAILY_LIMIT_MAX}",
             "quiet_from": "quiet hours need a time such as 22:00",
             "quiet_until": "quiet hours need a time such as 07:00"}


def _takes(name: str, value: Any) -> bool:
    """Whether `value` can be the preference `name`, as a request or a file edited by hand may say it."""
    if name == AI_DAILY_LIMIT:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 < value <= AI_DAILY_LIMIT_MAX
    if name == "undo_send_seconds":
        return isinstance(value, int) and not isinstance(value, bool) and send_window.SHORTEST <= value <= send_window.LONGEST
    if name in CHOICES:
        return value in CHOICES[name]
    if name in _TIMES:
        return isinstance(value, str) and _CLOCK.fullmatch(value) is not None
    return name in DEFAULTS and isinstance(value, bool)


def _block_of(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    block = raw.get(BLOCK)
    return block if is_object(block) else {}


def said(raw: Mapping[str, Any], name: str) -> Any:
    """What the file says of one preference, or None where it says nothing that preference can be."""
    value = _block_of(raw).get(name)
    return value if _takes(name, value) else None


def read(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Every preference as it stands: what the file says where that can be it, and else the default. It never raises."""
    there = _block_of(raw)
    return {name: there[name] if name in there and _takes(name, there[name]) else default for name, default in DEFAULTS.items()}


def changes(asked: Mapping[str, Any]) -> dict[str, Any]:
    """Of what a person asked, what is a preference, or why not: one value no preference can be refuses the whole request, so a
    change is all or nothing. A name that is no preference is ignored, as a request's other unknown names are."""
    wanted: dict[str, Any] = {}
    for name, value in asked.items():
        if name != AI_DAILY_LIMIT and name not in DEFAULTS:
            continue
        if not _takes(name, value):
            raise Refused(_REFUSALS.get(name, f"{name} can't be that"))
        wanted[name] = value
    return wanted


def importable(held: Mapping[str, Any], raw: Mapping[str, Any]) -> dict[str, Any]:
    """Of what a browser held under its old keys, what can be a preference and isn't one yet. What the file already says stands:
    the first browser to bring a value in sets it, and the next can't move it."""
    return {name: value for name, value in held.items() if name in DEFAULTS and _takes(name, value) and said(raw, name) is None}


def apply(raw: dict[str, Any], wanted: Mapping[str, Any]) -> None:
    """Write `wanted` into the file's contents, and nothing else. The block comes to be only when a preference goes in it."""
    for name, value in wanted.items():
        if name == AI_DAILY_LIMIT:
            raw[name] = value
            continue
        block = raw.get(BLOCK)
        if not is_object(block):
            block = raw[BLOCK] = {}
        block[name] = value
