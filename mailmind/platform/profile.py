"""How this process runs: its profile, `full` or `read-only`.

- `full` is the real thing. It syncs in the background, and it sends and files mail.
- `read-only` writes nothing to a mail server or anywhere else outside (decision D4): no IMAP
  change, no SMTP, no unsubscribe request. A change a person asks for that would reach a server
  (a send, a move or flag, a mute or snooze, a cleanup or sweep, a folder made, renamed or
  deleted, a draft's copy removed) is refused with ReadOnlyError before anything is queued or
  hidden here, so nothing waits for a run that writes; what a full run queued before waits for
  the next. What changes only this Mac still works: a draft saved here, reading, a rule, AI.
  Its one background job is snooze's, which touches the database alone: no sync, IDLE,
  reconcile, expiry, body backfill or reply drafting. It reads IMAP only when someone asks, by
  opening mail or a folder or starting a sync.

MAILMIND_PROFILE names the profile; unset or empty, it is `full`. MAILMIND_NOSYNC, the older
switch, still selects read-only, and a value it doesn't take fails toward read-only. When the two
disagree, read-only wins. Both are declared in mailmind.platform.environment, which reads them at
every call, so a test can change them.
"""
import enum

from mailmind.platform import environment
from mailmind.platform.environment import Environ
from mailmind.platform.settings import SettingsError


class Profile(enum.Enum):
    FULL = "full"
    READ_ONLY = "read-only"


class ProfileError(SettingsError):
    """MAILMIND_PROFILE names no profile."""


class ReadOnlyError(Exception):
    """A write to a mail server, or anywhere else outside, refused because the process runs
    read-only. Its message names what was refused."""


def current(environ: Environ | None = None) -> Profile:
    """The process's profile, from the environment (os.environ unless given). A MAILMIND_PROFILE
    that names no profile counts as read-only here, so it fails safe; check() refuses it."""
    if environment.NOSYNC.on(environ):
        return Profile.READ_ONLY
    return Profile(environment.PROFILE.value(environ))


def check(environ: Environ | None = None) -> Profile:
    """current(), but a MAILMIND_PROFILE that names no profile raises ProfileError. The app's
    start calls it before it serves."""
    if environment.PROFILE.named(environ) is None:
        names = ", ".join(p.value for p in Profile)
        raise ProfileError(f"{environment.PROFILE.name} must be one of {names}, not {environment.PROFILE.raw(environ)!r}")
    return current(environ)


def writes_allowed() -> bool:
    """Whether this process may write to a mail server or anywhere else outside: only a full one."""
    return current() is Profile.FULL


def refuse_writes(what: str) -> None:
    """Raise ReadOnlyError, naming `what`, unless this process may write."""
    if not writes_allowed():
        raise ReadOnlyError(what)
