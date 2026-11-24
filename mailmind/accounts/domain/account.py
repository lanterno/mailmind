"""An account as `mailmind.config.json` holds it: an entry's defaults, what the API may show of it, and
what an edit may change. Credential styles are mailmind.accounts.domain.credential's."""
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

from mailmind.accounts.domain.credential import CredentialMask, CredentialRef
from mailmind.kernel.parsed import as_int

# An entry of the file, or what a request asked of one: JSON as it parsed, so what a key holds is the file's
# word for it, Any on purpose. `normalized` says which keys the rest of the app may count on.
type Entry = dict[str, Any]

PORT_SAID = {"port": "port must be 1–65535", "smtp_port": "SMTP port must be 1–65535"}


class TypeDefaults(TypedDict):
    host: str | None
    port: int
    starttls: bool
    trash: str
    strip_spaces: bool
    smtp_host: str | None
    smtp_port: int
    smtp_starttls: bool


class MaskedRow(TypedDict):
    """An account as the API may show it. The handler must NEVER serialize a raw entry."""
    email: str | None
    type: str
    alias: str | None
    host: str | None
    port: int
    starttls: bool
    trash: str
    smtp_host: str | None
    smtp_port: int
    smtp_starttls: bool
    disabled: bool
    defaults_applied: dict[str, bool]
    credential: CredentialMask
    signature: str


TYPE_DEFAULTS: dict[str, TypeDefaults] = {
    "gmail":  {"host": "imap.gmail.com", "port": 993, "starttls": False,
               "trash": "[Gmail]/Trash", "strip_spaces": True,
               "smtp_host": "smtp.gmail.com", "smtp_port": 587, "smtp_starttls": True},
    "imap":   {"host": None, "port": 993, "starttls": False,
               "trash": "Trash", "strip_spaces": False,
               "smtp_host": None, "smtp_port": 587, "smtp_starttls": True},
    "proton": {"host": "127.0.0.1", "port": 1143, "starttls": True,
               "trash": "Trash", "strip_spaces": False,
               "smtp_host": "127.0.0.1", "smtp_port": 1025, "smtp_starttls": True},
}
# SMTP transport keys editable per account; smtp_host=None means "reuse the IMAP host".
_SMTP_KEYS = ("smtp_host", "smtp_port", "smtp_starttls")


@dataclass(frozen=True)
class Endpoint:
    """Where a server listens, and how TLS starts there. Whatever the entry leaves out comes from its type's defaults."""
    host: str | None
    port: int
    starttls: bool


def _defaults(entry: Mapping[str, Any]) -> TypeDefaults:
    return TYPE_DEFAULTS.get(entry.get("type", "imap"), TYPE_DEFAULTS["imap"])


def imap_endpoint(entry: Mapping[str, Any]) -> Endpoint:
    d = _defaults(entry)
    return Endpoint(entry.get("host", d["host"]), entry.get("port", d["port"]), entry.get("starttls", d["starttls"]))


def smtp_endpoint(entry: Mapping[str, Any]) -> Endpoint:
    """An explicit override, else the type's default; generic IMAP has none, so it is the IMAP host. So an account with
    an IMAP endpoint always has an SMTP one."""
    d = _defaults(entry)
    return Endpoint(entry.get("smtp_host") or d["smtp_host"] or imap_endpoint(entry).host,
                    entry.get("smtp_port", d["smtp_port"]), entry.get("smtp_starttls", d["smtp_starttls"]))


def normalized(a: Entry) -> Entry | None:
    """An account's entry in the file as the rest of the app reads it, its type's defaults filled in.
    None for one that can't be used: disabled, one with no address, or a generic IMAP account with no host."""
    if a.get("disabled") or not a.get("email"):
        return None
    imap, smtp, d = imap_endpoint(a), smtp_endpoint(a), _defaults(a)
    if not imap.host:
        return None
    return {
        "email": a["email"], "kind": a.get("type", "imap"), "host": imap.host,
        "port": imap.port, "starttls": imap.starttls,
        "trash": a.get("trash", d["trash"]), "strip_spaces": a.get("strip_spaces", d["strip_spaces"]),
        "smtp_host": smtp.host, "smtp_port": smtp.port, "smtp_starttls": smtp.starttls,
        "alias": (a.get("alias") or "").strip() or None,
        "credential": a.get("credential", {}),
        # "imapclient" asks for IMAPClient (mailmind.bootstrap.mailbox)
        "imap_adapter": a.get("imap_adapter"),
        # true asks for what MAILMIND_POOL and MAILMIND_WARM_FOLDERS give it, when neither is set
        "pool": a.get("pool") is True, "warm_folders": a.get("warm_folders") is True,
    }


def masked_row(entry: Entry) -> MaskedRow:
    """Re-shape ONE raw account entry into the secret-free row the API returns.
    Echoes effective host/port/etc (with a defaults_applied flag) and masks the
    credential. The handler must NEVER serialize a raw entry directly."""
    imap, smtp = imap_endpoint(entry), smtp_endpoint(entry)
    applied = {k: (k not in entry) for k in ("host", "port", "starttls", "trash", *_SMTP_KEYS)}
    return {
        "email": entry.get("email"), "type": entry.get("type", "imap"),
        "alias": (entry.get("alias") or "").strip() or None,
        "host": imap.host, "port": imap.port,
        "starttls": imap.starttls, "trash": entry.get("trash", _defaults(entry)["trash"]),
        "smtp_host": smtp.host, "smtp_port": smtp.port, "smtp_starttls": smtp.starttls,
        "disabled": bool(entry.get("disabled")),
        "defaults_applied": applied,
        "credential": CredentialRef.parse(entry.get("credential")).masked(),
        "signature": (entry.get("signature") or ""),   # plain text, intentionally non-secret
    }


def find(raw: Entry, email: str | None) -> Entry | None:
    el = (email or "").strip().lower()
    for e in raw.get("accounts", []):
        if (e.get("email") or "").strip().lower() == el:
            return e
    return None


def colour_places(entries: list[Entry]) -> dict[str, int]:
    """Which of the palette's colours each account wears, by address: a place both clients take modulo their palette. An
    entry's own `colour`, a whole number no entry before it took, stands; every other takes the first place free, in the
    file's order, so a file that keeps none colours the accounts by their order. `keep_colours` writes them down, so that
    removing an account leaves every other's as it was."""
    places: dict[str, int] = {}
    for e in entries:
        own, email = e.get("colour"), e.get("email")
        if email and email not in places and type(own) is int and own >= 0 and own not in places.values():
            places[email] = own
    for e in entries:
        if (email := e.get("email")) and email not in places:
            taken = set(places.values())
            places[email] = next(n for n in range(len(entries) + 1) if n not in taken)
    return places


def keep_colours(entries: list[Entry]) -> None:
    """Write each account's place into its entry, as it is now: added or removed, an account moves no other's colour."""
    places = colour_places(entries)
    for e in entries:
        if e.get("email") in places:
            e["colour"] = places[e["email"]]


def clean_alias(a: object) -> str | None:
    if a is None:
        return None
    a = "".join(ch for ch in str(a) if ch == " " or ch.isprintable())
    return a.strip()[:64]


def clean_signature(s: object) -> str:
    """Signatures are multiline plain text (non-secret) inserted into outgoing bodies.
    Preserve newlines + printable chars; strip control/bidi/zero-width smuggling; cap length."""
    if s is None:
        return ""
    out = "".join(ch for ch in str(s) if ch in ("\n", "\r", "\t") or ch.isprintable())
    return out.strip()[:2000]


def apply_fields(entry: Entry, p: Mapping[str, Any]) -> None:
    """Apply editable non-credential fields from payload p onto entry in place."""
    if "alias" in p:
        al = clean_alias(p.get("alias"))
        if al:
            entry["alias"] = al
        else:
            entry.pop("alias", None)  # blank => unset (don't store "")
    if "signature" in p:
        sg = clean_signature(p.get("signature"))
        if sg:
            entry["signature"] = sg
        else:
            entry.pop("signature", None)  # blank => unset
    for k in ("type", "host", "trash", "smtp_host"):
        if k in p and p[k] not in (None, ""):
            entry[k] = p[k]
    for k in ("port", "smtp_port"):
        if k in p and p[k] not in (None, ""):
            entry[k] = _port(k, p[k])
    for k in ("starttls", "strip_spaces", "disabled", "smtp_starttls"):
        if k in p and isinstance(p[k], bool):
            entry[k] = p[k]


def _port(key: str, given: object) -> int:
    number = as_int(given)
    if number is None:
        raise ValueError(PORT_SAID[key])
    return number


def validate_new(entry: Entry) -> None:
    email = (entry.get("email") or "").strip()
    if "@" not in email:
        raise ValueError("a valid email is required")
    t = entry.get("type", "imap")
    if t not in TYPE_DEFAULTS:
        raise ValueError("type must be gmail, imap, or proton")
    if t == "imap" and not str(entry.get("host") or "").strip():
        raise ValueError("a host is required for a generic IMAP account")
    if "port" in entry and not (1 <= _port("port", entry["port"]) <= 65535):
        raise ValueError(PORT_SAID["port"])


def _names_a_host(held: object) -> bool:
    return isinstance(held, str) and bool(held) and all(ch.isprintable() and not ch.isspace() for ch in held)


def refuse_bad_endpoints(entry: Entry, before: Mapping[str, Any]) -> None:
    """A write may not leave an endpoint no connection could reach. Only what the write changes counts: what the file
    held `before` it is the owner's, and asking for a new alias is no time to refuse it."""
    def changed(key: str) -> bool:
        return key in entry and entry[key] != before.get(key)

    if changed("host") and not _names_a_host(entry["host"]):
        raise ValueError("host must be a host name")
    if changed("smtp_host") and not _names_a_host(entry["smtp_host"]):
        raise ValueError("SMTP host must be a host name")
    if changed("smtp_port") and not (1 <= _port("smtp_port", entry["smtp_port"]) <= 65535):
        raise ValueError(PORT_SAID["smtp_port"])


def strip_for(entry: Entry) -> bool:
    return entry.get("strip_spaces", _defaults(entry)["strip_spaces"])


APP_PASSWORD_LETTERS = 16
_SPACES = (" ", "\u00a0")       # a space, and the no-break space a copy from a web page may bring


def as_kept(typed: str, strip_spaces: object) -> str:
    """A password typed for an account, as it is kept and sent. Google shows an App Password as four groups of four
    letters with spaces between, and an account that strips spaces (`strip_spaces`, a Gmail account's by default) keeps it
    without them; any other password, there or elsewhere, is kept as it was typed, its spaces too."""
    if not strip_spaces:
        return typed
    bare = "".join(ch for ch in typed if ch not in _SPACES)
    return bare if len(bare) == APP_PASSWORD_LETTERS and bare.isascii() and bare.isalpha() else typed
