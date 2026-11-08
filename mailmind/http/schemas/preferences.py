"""What the page asks of a person's preferences, and what it answers (`mailmind.accounts.domain.preferences`)."""
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator

from mailmind.accounts.api import Density, MailColors, ReadFont, ReadSize, ReadWidth, SearchScope, Theme
from mailmind.assistant.api import DraftTone
from mailmind.http.schemas.common import Answer, Flag, Number, RequestBody


def _dollars(given):
    if isinstance(given, bool) or not isinstance(given, int | float):
        raise ValueError("not a number")
    return float(given)


# Dollars, as a page's number field sends them. Lax, pydantic would read true as 1.0, and "12" as 12.
Dollars = Annotated[float, BeforeValidator(_dollars)]


class ChangedPreferences(RequestBody):
    """What changes. What a request doesn't name keeps what it was, so two tabs can each change their own."""
    undo_send_seconds: Number | None = None
    density: Density | None = None
    high_contrast: Flag | None = None
    theme: Theme | None = None
    read_width: ReadWidth | None = None
    read_size: ReadSize | None = None
    read_font: ReadFont | None = None
    mail_colors: MailColors | None = None
    single_keys: Flag | None = None
    conversations: Flag | None = None
    search_scope: SearchScope | None = None
    draft_tone: DraftTone | None = None
    notify_new_mail: Flag | None = None
    notify_meetings: Flag | None = None
    quiet_hours: Flag | None = None
    quiet_from: str | None = None
    quiet_until: str | None = None
    guide_offered: Flag | None = None
    morning_brief: Flag | None = None
    ai_daily_limit_usd: Dollars | None = None


class HeldPreferences(RequestBody):
    """What a browser kept under its old keys, once, in the block's words. Any value: one no preference can be is left out, since
    nobody asked for it to be refused."""
    held: dict[str, Any]


class Preferences(Answer):
    """Every preference, as it stands: what the file says, or the default."""
    undo_send_seconds: int
    density: Density
    high_contrast: bool
    theme: Theme
    read_width: ReadWidth
    read_size: ReadSize
    read_font: ReadFont
    mail_colors: MailColors
    single_keys: bool
    conversations: bool
    search_scope: SearchScope
    draft_tone: DraftTone
    notify_new_mail: bool
    notify_meetings: bool
    quiet_hours: bool
    quiet_from: str
    quiet_until: str
    guide_offered: bool
    morning_brief: bool
    ai_daily_limit_usd: float


class PreferencesAnswer(Answer):
    ok: Literal[True]
    preferences: Preferences
