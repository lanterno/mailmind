"""What a person has set for how MailMind looks and behaves: the "preferences" block of mailmind.config.json, beside the accounts.

A name the block doesn't hold, or holds something that can't be that preference (a hand edit), is that preference's default, so a
file with no block goes on exactly as before and reading one writes nothing. A change names only what changes and leaves the rest
of the file as it stands. The AI's daily limit is one more setting, but the file keeps it at the top level, as it always did
("ai_daily_limit_usd"), where the Assistant reads it. Whether AI is on, its provider, model and service URL are preferences like
the rest (#49): "" for a model or URL is the provider's own, and choosing another provider sets the last one's model and URL back
to "", unless the same change names them, so one provider's are never sent to another. A model on this Mac is reached on this Mac
alone: a URL elsewhere is refused for it, and one a hand edit gave it reads as its own.

Each closed set is a `type`, so a request's schema takes it by name and the page has it as a type and keeps no list of its own.
"""
import math
import re
from collections.abc import Mapping
from typing import Any, Literal, get_args
from urllib.parse import urlsplit

from mailmind.kernel import send_window
from mailmind.kernel.ai_provider import AI_PROVIDERS, DEFAULT_PROVIDER
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
    "ai_provider": AI_PROVIDERS,
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
    "ai_enabled": True,
    "ai_provider": DEFAULT_PROVIDER,
    "ai_model": "",                         # the provider's own
    "ai_base_url": "",                      # the provider's own
}
AI_DAILY_LIMIT = "ai_daily_limit_usd"       # at the file's top level, not in the block
AI_DAILY_LIMIT_MAX = 1000                   # dollars: past this is a slip of the keyboard, not a budget
AI_MODEL_MAX = 200                          # characters in a model's name
AI_BASE_URL_MAX = 500                       # characters in a service's URL
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})     # the hosts an http URL may name: this Mac
BLOCK = "preferences"

_FOLLOW_PROVIDER = ("ai_model", "ai_base_url")             # a provider's own, set back when another is chosen
_TIMES = ("quiet_from", "quiet_until")
_CLOCK = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")
_MODEL = re.compile(rf"[A-Za-z0-9._:/@+-]{{1,{AI_MODEL_MAX}}}")
_REFUSALS = {"undo_send_seconds": f"the Undo Send window must be {send_window.SHORTEST} to {send_window.LONGEST} seconds",
             AI_DAILY_LIMIT: f"the AI daily limit must be more than $0 and at most ${AI_DAILY_LIMIT_MAX}",
             "quiet_from": "quiet hours need a time such as 22:00",
             "quiet_until": "quiet hours need a time such as 07:00",
             "ai_model": f"the AI model must be up to {AI_MODEL_MAX} letters, digits and . _ : / @ + -",
             "ai_base_url": f"the AI service's URL must be https, or http on this Mac, in at most {AI_BASE_URL_MAX} "
                            "characters, with no user, password, query or fragment"}
LOCAL_ELSEWHERE = "A model on this Mac must be at 127.0.0.1, ::1 or localhost."


def _service_url(value: str) -> bool:
    """Whether `value` can be an AI service's URL: https to a host, or http to this Mac, in printable ASCII, with no user or
    password, query or fragment."""
    if len(value) > AI_BASE_URL_MAX or any(not "!" <= ch <= "~" for ch in value) or "?" in value or "#" in value:
        return False
    try:
        parts = urlsplit(value)
        parts.port                                      # a port that is no number, or past 65535, raises
    except ValueError:
        return False
    if "@" in parts.netloc or not parts.hostname:
        return False
    return parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOOPBACK)


def _on_this_mac(url: str) -> bool:
    """Whether a URL that can be a service's names this Mac."""
    try:
        return urlsplit(url).hostname in LOOPBACK
    except ValueError:
        return False


def _here_for_local(prefs: Mapping[str, Any]) -> bool:
    """Whether the provider and URL go together: a model on this Mac only at a URL on this Mac, or at its own."""
    return prefs["ai_provider"] != "local" or not prefs["ai_base_url"] or _on_this_mac(prefs["ai_base_url"])


def _kept(name: str, value: Any) -> Any:
    """`value` as the file keeps it and a reader is given it: a URL without its trailing /."""
    return value.rstrip("/") if name == "ai_base_url" else value


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
    if name == "ai_model":
        return isinstance(value, str) and (value == "" or _MODEL.fullmatch(value) is not None)
    if name == "ai_base_url":
        return isinstance(value, str) and (value == "" or _service_url(value))
    return name in DEFAULTS and isinstance(value, bool)


def _block_of(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    block = raw.get(BLOCK)
    return block if is_object(block) else {}


def said(raw: Mapping[str, Any], name: str) -> Any:
    """What the file says of one preference, or None where it says nothing that preference can be."""
    value = _block_of(raw).get(name)
    return _kept(name, value) if _takes(name, value) else None


def read(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Every preference as it stands: what the file says where that can be it, and else the default. It never raises."""
    there = _block_of(raw)
    prefs = {name: _kept(name, there[name]) if name in there and _takes(name, there[name]) else default
             for name, default in DEFAULTS.items()}
    if not _here_for_local(prefs):
        prefs["ai_base_url"] = DEFAULTS["ai_base_url"]
    return prefs


def changes(asked: Mapping[str, Any], raw: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Of what a person asked, what is a preference, or why not: one value no preference can be refuses the whole request, so a
    change is all or nothing. A name that is no preference is ignored, as a request's other unknown names are. Choosing a
    provider other than the one the file `raw` holds sets its model and URL back to the provider's own, where the change doesn't
    name them. A model on this Mac at a URL elsewhere is refused, whichever of the two the change names."""
    wanted: dict[str, Any] = {}
    for name, value in asked.items():
        if name != AI_DAILY_LIMIT and name not in DEFAULTS:
            continue
        if not _takes(name, value):
            raise Refused(_REFUSALS.get(name, f"{name} can't be that"))
        wanted[name] = _kept(name, value)
    now = read(raw or {})
    if "ai_provider" in wanted and wanted["ai_provider"] != now["ai_provider"]:
        for name in _FOLLOW_PROVIDER:
            wanted.setdefault(name, "")
    if not _here_for_local({**now, **wanted}):
        raise Refused(LOCAL_ELSEWHERE)
    return wanted


def importable(held: Mapping[str, Any], raw: Mapping[str, Any]) -> dict[str, Any]:
    """Of what a browser held under its old keys, what can be a preference and isn't one yet. What the file already says stands:
    the first browser to bring a value in sets it, and the next can't move it."""
    return {name: _kept(name, value) for name, value in held.items()
            if name in DEFAULTS and _takes(name, value) and said(raw, name) is None}


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
