"""Addresses as a person types them: split, validated and IDNA-normalized, and free of what must
never reach a header. Standard library only."""
import email.utils
import re

MAX_RECIPIENTS = 100
# Codepoints that must never survive into a header value: C0/C1 controls, bidi
# overrides/isolates, zero-width, and line/paragraph separators (homograph + smuggling).
_DANGER = re.compile(
    "[\x00-\x1f\x7f-\x9f​-‏‪-‮⁠⁦-⁩  ﻿]")
# An addr-spec may not contain whitespace, separators, brackets, or control chars.
_ADDR_BAD = re.compile(r'[\s,;:<>()\[\]\\"]|[\x00-\x1f\x7f]')

def clean(s):
    """Strip dangerous codepoints from a free-text header value (subject/display name)."""
    return _DANGER.sub("", (s or "").replace("\t", " ")).strip()


def idna_domain(domain):
    """ASCII (IDNA) form of a domain, or raise ValueError. ASCII domains pass through."""
    domain = (domain or "").strip().rstrip(".")
    if not domain or "." not in domain and domain.lower() not in ("localhost",):
        raise ValueError("invalid domain")
    try:
        return domain.encode("idna").decode("ascii")
    except (UnicodeError, Exception):
        raise ValueError(f"invalid domain: {domain!r}")


def validate_pair(name, addr):
    """(clean_display_name, addr_spec_ascii) from an already-split (name, addr), or
    raise ValueError. addr_spec is normalized to local@idna-domain."""
    addr = (addr or "").strip()
    if not addr or addr.count("@") != 1:
        raise ValueError(f"not a valid address: {addr!r}")
    # reject bidi/zero-width/control codepoints anywhere in the addr-spec (homograph +
    # non-ASCII envelope that would crash smtplib's ascii encoder mid-transaction)
    if _DANGER.search(addr):
        raise ValueError(f"unsafe characters in address: {addr!r}")
    local, _, domain = addr.rpartition("@")
    if not local or _ADDR_BAD.search(local) or _ADDR_BAD.search(domain):
        raise ValueError(f"not a valid address: {addr!r}")
    return clean(name), f"{local}@{idna_domain(domain)}"


def parse_addr_list(raw):
    """Validate a raw recipient string OR a list of tokens -> [(name, addr_spec)].
    Uses getaddresses so a comma inside a quoted display name is not mis-split."""
    tokens = raw if isinstance(raw, list) else [raw or ""]
    out, seen = [], set()
    for name, addr in email.utils.getaddresses([t for t in tokens if t]):
        dn, spec = validate_pair(name, addr)   # validate the split pair directly
        key = spec.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((dn, spec))
    return out
