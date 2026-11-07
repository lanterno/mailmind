"""A login its server refused isn't sent again straight away.

A server counts refused logins, and enough of them locks the account out, or the address they came from. Everything
that talks to a server opens its own connection (the sync, IDLE, the body backfill, the queue's retries, a person
opening a message), so a password gone bad would otherwise be sent every minute or so for as long as MailMind runs.

So a refusal holds that login back for a while: five minutes, then twice that each time the next try is refused too,
up to an hour, and nothing is sent until then. A hold names one login, the server, the address and the credential
together, so another of any of them isn't held. A server saying it's down for now ([UNAVAILABLE], RFC 5530) is no
refusal, and neither is a connection lost during LOGIN.
"""
import hashlib
import hmac
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

FIRST = 300
LONGEST = 3600

_KEY = secrets.token_bytes(32)    # this process's own, so a login's id says nothing of its credential to anyone else


def refuses(said: str) -> bool:
    """Whether what a server answered LOGIN with is a refusal, rather than word that it can't say for now."""
    return "[UNAVAILABLE]" not in said.upper()


def login_id(acct: Mapping[str, Any]) -> str:
    """Names one way of logging in, so two can be compared without keeping a password to compare."""
    what = [acct.get(k) for k in ("email", "host", "port", "starttls", "strip_spaces", "credential")]
    return hmac.new(_KEY, json.dumps(what, sort_keys=True, default=str).encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class Hold:
    login: str
    until: float
    rest: int

    def stops(self, login: str, now: float) -> bool:
        return login == self.login and now < self.until


def after_refusal(before: Hold | None, login: str, now: float) -> Hold:
    """The hold a refusal of `login` leaves, given the one there was. Two tries that crossed, or a person's test during
    a hold, count for one: only a try let through after a hold ended doubles it."""
    if before is None or before.login != login:
        rest = FIRST
    elif now < before.until:
        rest = before.rest
    else:
        rest = min(before.rest * 2, LONGEST)
    return Hold(login, now + rest, rest)
