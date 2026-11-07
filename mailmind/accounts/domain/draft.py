"""An account not saved yet, as a test of its login tries it.

A draft may only try a password typed with it, against the host typed with it. One naming a stored secret (an op
reference, an environment variable, the Bridge password) would send that secret to whatever host the request names,
so it is refused until the account is saved. A saved account's credential goes only to its saved server, and that's
the use case's to see to: it never reads an endpoint out of the request.
"""
from collections.abc import Mapping
from typing import Any

from mailmind.accounts.domain.account import TYPE_DEFAULTS, Entry


class DraftRefused(Exception):
    """A draft that can't be tried. Its text is one of the fixed sentences below: it is shown, and holds nothing typed."""


def throwaway_account(draft: Mapping[str, Any]) -> Entry:
    """What a draft asks to try, as an account a connection can open, and that nothing keeps."""
    ty = draft.get("type", "imap")
    td = TYPE_DEFAULTS.get(ty, TYPE_DEFAULTS["imap"])
    cs: Entry = draft.get("credential") or {}
    acct: Entry = {"email": draft.get("email"), "kind": ty,
                   "host": draft.get("host") or td["host"], "port": int(draft.get("port") or td["port"]),
                   "starttls": bool(draft.get("starttls", td["starttls"])),
                   "strip_spaces": bool(draft.get("strip_spaces", td["strip_spaces"])),
                   "trash": td["trash"], "credential": {"plain": cs.get("value")}}
    if not acct["host"]:
        raise DraftRefused("a host is required")
    if cs.get("style") != "plain" or not cs.get("value"):
        raise DraftRefused("save the account to test a stored secret; before then only a typed password is tried")
    return acct
