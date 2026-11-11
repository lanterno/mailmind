"""An account not saved yet, as a test of its login tries it.

A draft may only try a password typed with it, against the host typed with it. One naming a stored secret (an op
reference, an environment variable, the Bridge password) would send that secret to whatever host the request names,
so it is refused until the account is saved. A saved account's credential goes only to its saved server, and that's
the use case's to see to: it never reads an endpoint out of the request.
"""
from collections.abc import Mapping
from typing import Any

from mailmind.accounts.domain.account import PORT_SAID, TYPE_DEFAULTS, Entry
from mailmind.accounts.domain.credential import Typed
from mailmind.kernel.parsed import as_int


class DraftRefused(Exception):
    """A draft that can't be tried. Its text is one of the fixed sentences below: it is shown, and holds nothing typed."""


class BadPort(ValueError):
    """A draft's port no connection could reach: a failed test, in the sentence the edit of an account says it in."""


def _port_of(draft: Mapping[str, Any], default: int) -> int:
    number = as_int(draft.get("port") or default)
    if number is None or not 1 <= number <= 65535:
        raise BadPort(PORT_SAID["port"])
    return number


def throwaway_account(draft: Mapping[str, Any]) -> Entry:
    """What a draft asks to try, as an account a connection can open, and that nothing keeps: its password is `Typed`,
    which the Keychain never sees and no file can hold."""
    ty = draft.get("type", "imap")
    td = TYPE_DEFAULTS.get(ty, TYPE_DEFAULTS["imap"])
    cs: Entry = draft.get("credential") or {}
    typed = cs.get("value")
    acct: Entry = {"email": draft.get("email"), "kind": ty,
                   "host": draft.get("host") or td["host"], "port": _port_of(draft, td["port"]),
                   "starttls": bool(draft.get("starttls", td["starttls"])),
                   "strip_spaces": bool(draft.get("strip_spaces", td["strip_spaces"])),
                   "trash": td["trash"], "credential": Typed(typed if isinstance(typed, str) else "")}
    if not acct["host"]:
        raise DraftRefused("a host is required")
    if cs.get("style") not in ("keychain", "plain") or not (isinstance(typed, str) and typed):
        raise DraftRefused("save the account to test a stored secret; before then only a typed password is tried")
    return acct
