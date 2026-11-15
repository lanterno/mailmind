"""Which preset an address's account takes, from its domain: Gmail's for Google's own domains, and Gmail's offered for a
domain whose mail Google's servers take (a Google Workspace domain's), as its MX records name them. An offer is only that:
the person chooses, and nothing is guessed that they can't undo.

A domain is looked up only when it reads as one a lookup could ask about: ASCII labels of letters, digits and hyphens, two
or more of them, at most 253 characters. Anything else, a name in another script included, is no domain here, and offers
nothing.
"""
import re
from typing import Literal

type Preset = Literal["gmail"]

GOOGLE_DOMAINS = ("gmail.com", "googlemail.com")       # Google's own addresses: their accounts are Gmail's
GOOGLE_MAIL_HOSTS = ("google.com", "googlemail.com")   # an MX record naming one of these, or a host under it, is Google's
LONGEST_DOMAIN = 253
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def domain_of(email: str) -> str | None:
    """The domain an address names, in lower case and without a final dot, when it reads as one; else None."""
    _, at, domain = email.strip().rpartition("@")
    if not (at and domain.isascii()):             # lowered, a letter of another script may come out as an ASCII one
        return None
    domain = domain.lower().removesuffix(".")
    labels = domain.split(".")
    if len(domain) > LONGEST_DOMAIN or len(labels) < 2 or not all(_LABEL.fullmatch(one) for one in labels):
        return None
    return domain


def names_google(host: str) -> bool:
    """Whether an MX record's host is one of Google's mail servers."""
    host = host.lower().removesuffix(".")
    return any(host == google or host.endswith("." + google) for google in GOOGLE_MAIL_HOSTS)
