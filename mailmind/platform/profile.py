"""How this process runs: its profile, `full` or `read-only`.

- `full` is the real thing. It syncs in the background, and it sends and files mail.
- `read-only` writes nothing to a mail server or anywhere else outside (decision D4): no IMAP
  change, no SMTP, no unsubscribe request. Sends and moves wait in their queues for a full run,
  and what can't wait there is refused with ReadOnlyError. It starts no background work: no sync,
  IDLE, reconcile, expiry, body backfill or reply drafting. It reads IMAP only when someone asks,
  by opening mail or a folder or starting a sync.

MAILMIND_PROFILE names the profile; unset or empty, it is `full`. MAILMIND_NOSYNC, the older
switch, still selects read-only: any value but empty, 0, false, no or off turns it on, so a typo
fails toward read-only. When the two disagree, read-only wins. This module is the only reader of
both, and it reads them at every call, so a test can change them.

Standard library only: python3 server.py uses it.
"""
import enum
import os
from collections.abc import Mapping

from mailmind.platform.settings import SettingsError


class Profile(enum.Enum):
    FULL = "full"
    READ_ONLY = "read-only"


class ProfileError(SettingsError):
    """MAILMIND_PROFILE names no profile."""


class ReadOnlyError(Exception):
    """A write to a mail server, or anywhere else outside, refused because the process runs
    read-only. Its message names what was refused."""


_OFF = ("", "0", "false", "no", "off")


def _named(env: Mapping[str, str]) -> Profile | None:
    """The profile MAILMIND_PROFILE names, `full` when it names none, None when it isn't a name."""
    raw = env.get("MAILMIND_PROFILE", "").strip().lower()
    if not raw:
        return Profile.FULL
    try:
        return Profile(raw)
    except ValueError:
        return None


def current(environ: Mapping[str, str] | None = None) -> Profile:
    """The process's profile, from the environment (os.environ unless given). A MAILMIND_PROFILE
    that names no profile counts as read-only here, so it fails safe; check() refuses it."""
    env = os.environ if environ is None else environ
    if env.get("MAILMIND_NOSYNC", "").strip().lower() not in _OFF:
        return Profile.READ_ONLY
    return _named(env) or Profile.READ_ONLY


def check(environ: Mapping[str, str] | None = None) -> Profile:
    """current(), but a MAILMIND_PROFILE that names no profile raises ProfileError. Each front
    door calls it before it serves."""
    env = os.environ if environ is None else environ
    if _named(env) is None:
        names = ", ".join(p.value for p in Profile)
        raise ProfileError(f"MAILMIND_PROFILE must be one of {names}, not {env['MAILMIND_PROFILE']!r}")
    return current(env)


def writes_allowed() -> bool:
    """Whether this process may write to a mail server or anywhere else outside: only a full one."""
    return current() is Profile.FULL


def refuse_writes(what: str) -> None:
    """Raise ReadOnlyError, naming `what`, unless this process may write."""
    if not writes_allowed():
        raise ReadOnlyError(what)
