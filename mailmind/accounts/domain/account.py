"""An account as `mailmind.config.json` holds it: an entry's defaults, what the API may show of it, and
what an edit may change. Credential styles are mailmind.accounts.adapters.secrets's."""
from collections.abc import Mapping
from typing import Any, Required, TypedDict

# An entry of the file, or what a request asked of one: JSON as it parsed, so what a key holds is the file's
# word for it, Any on purpose. `normalized` says which keys the rest of the app may count on.
type Entry = dict[str, Any]
type Credential = str | dict[str, Any] | None


class TypeDefaults(TypedDict):
    host: str | None
    port: int
    starttls: bool
    trash: str
    strip_spaces: bool
    smtp_host: str | None
    smtp_port: int
    smtp_starttls: bool


class CredentialMask(TypedDict, total=False):
    """A credential as the API may show it: its style and whether it's set, and only ever a pointer."""
    style: Required[str | None]
    set: Required[bool]
    ref: str
    var: str


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


def normalized(a: Entry) -> Entry | None:
    """An account's entry in the file as the rest of the app reads it, its type's defaults filled in.
    None for one that can't be used: disabled, or a generic IMAP account with no host."""
    if a.get("disabled"):
        return None
    t = a.get("type", "imap")
    d = TYPE_DEFAULTS.get(t, TYPE_DEFAULTS["imap"])
    host = a.get("host", d["host"])
    if not host:
        return None
    return {
        "email": a["email"], "kind": t, "host": host,
        "port": a.get("port", d["port"]), "starttls": a.get("starttls", d["starttls"]),
        "trash": a.get("trash", d["trash"]), "strip_spaces": a.get("strip_spaces", d["strip_spaces"]),
        # SMTP transport: explicit override, else the type default; for generic
        # IMAP the default smtp_host is None, so fall back to the IMAP host.
        "smtp_host": a.get("smtp_host") or d["smtp_host"] or host,
        "smtp_port": a.get("smtp_port", d["smtp_port"]),
        "smtp_starttls": a.get("smtp_starttls", d["smtp_starttls"]),
        "alias": (a.get("alias") or "").strip() or None,
        "credential": a.get("credential", {}),
        # "imapclient" asks the new door for IMAPClient (mailmind.bootstrap.mailbox)
        "imap_adapter": a.get("imap_adapter"),
    }


def mask_credential(cred: Credential) -> CredentialMask:
    """Public, secret-FREE descriptor of a credential. NEVER returns a value,
    length, prefix, or hash — only the style and whether something is set."""
    if isinstance(cred, str):
        return {"style": "plain", "set": bool(cred)}
    if not isinstance(cred, dict) or not cred:
        return {"style": None, "set": False}
    if "plain" in cred:
        return {"style": "plain", "set": bool(cred.get("plain"))}
    if "op" in cred:
        return {"style": "op", "set": bool(cred["op"]), "ref": cred["op"]}  # ref is a pointer, safe
    if "env" in cred:
        return {"style": "env", "set": bool(cred["env"]), "var": cred["env"]}
    if cred.get("proton_bridge"):
        return {"style": "proton_bridge", "set": True}
    return {"style": "unknown", "set": False}


def masked_row(entry: Entry) -> MaskedRow:
    """Re-shape ONE raw account entry into the secret-free row the API returns.
    Echoes effective host/port/etc (with a defaults_applied flag) and masks the
    credential. The handler must NEVER serialize a raw entry directly."""
    t = entry.get("type", "imap")
    d = TYPE_DEFAULTS.get(t, TYPE_DEFAULTS["imap"])
    applied = {k: (k not in entry) for k in ("host", "port", "starttls", "trash", *_SMTP_KEYS)}
    eff_host = entry.get("host", d["host"])
    return {
        "email": entry.get("email"), "type": t,
        "alias": (entry.get("alias") or "").strip() or None,
        "host": eff_host, "port": entry.get("port", d["port"]),
        "starttls": entry.get("starttls", d["starttls"]), "trash": entry.get("trash", d["trash"]),
        "smtp_host": entry.get("smtp_host") or d["smtp_host"] or eff_host,
        "smtp_port": entry.get("smtp_port", d["smtp_port"]),
        "smtp_starttls": entry.get("smtp_starttls", d["smtp_starttls"]),
        "disabled": bool(entry.get("disabled")),
        "defaults_applied": applied,
        "credential": mask_credential(entry.get("credential")),
        "signature": (entry.get("signature") or ""),   # plain text, intentionally non-secret
    }


def find(raw: Entry, email: str | None) -> Entry | None:
    el = (email or "").strip().lower()
    for e in raw.get("accounts", []):
        if (e.get("email") or "").strip().lower() == el:
            return e
    return None


def credential_from(p: Entry | None) -> dict[str, Any] | None:
    """Build a stored credential dict from a request payload {style,value|ref|var}.
    Returns None to mean 'keep existing'. Raises ValueError on invalid input."""
    if not p:
        return None
    style = p.get("style")
    if style == "plain":
        v = p.get("value")
        if not v:  # never blank out / store an empty password
            raise ValueError("password required")
        return {"plain": v}
    if style == "op":
        ref = p.get("ref")
        if not ref or (isinstance(ref, str) and not ref.strip()):
            raise ValueError("1Password reference required")
        return {"op": ref}
    if style == "env":
        var = (p.get("var") or "").strip()
        if not var:
            raise ValueError("environment variable name required")
        return {"env": var}
    if style == "proton_bridge":
        return {"proton_bridge": True}
    return None  # style unchanged / not provided -> keep existing


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
            entry[k] = int(p[k])
    for k in ("starttls", "strip_spaces", "disabled", "smtp_starttls"):
        if k in p and isinstance(p[k], bool):
            entry[k] = p[k]


def validate_new(entry: Entry) -> None:
    email = (entry.get("email") or "").strip()
    if "@" not in email:
        raise ValueError("a valid email is required")
    t = entry.get("type", "imap")
    if t not in TYPE_DEFAULTS:
        raise ValueError("type must be gmail, imap, or proton")
    if t == "imap" and not (entry.get("host") or "").strip():
        raise ValueError("a host is required for a generic IMAP account")
    if "port" in entry and not (1 <= int(entry["port"]) <= 65535):
        raise ValueError("port must be 1–65535")


def strip_for(entry: Entry) -> bool:
    t = entry.get("type", "imap")
    return entry.get("strip_spaces", TYPE_DEFAULTS.get(t, TYPE_DEFAULTS["imap"])["strip_spaces"])
