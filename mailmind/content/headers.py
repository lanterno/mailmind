"""Headers of a parsed message as people read them, and a recipient as the composer holds one."""
import email.utils
import re

from mailmind.content.mime import AnyMessage

_CHIP_QUOTE = re.compile(r'[()<>\[\]:;@\\,."]')


def addr_chip(name: str | None, addr: str) -> str:
    """A recipient the way the composer holds one: the bare address, or Name <address> with
    the name quoted when it has a character an address list would split on. Round-trips
    through parse_addr_list."""
    name = (name or "").strip()
    if not name or name.lower() == (addr or "").lower():
        return addr
    if _CHIP_QUOTE.search(name):
        name = '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return f"{name} <{addr}>"


def unsurrogate(s: str) -> str:
    """A header written in raw UTF-8 (RFC 6532) as text: policy.default keeps its 8-bit bytes as
    lone surrogates, which would show as replacement marks and go out as "unknown-8bit"."""
    try:
        return s.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    except UnicodeError:
        return s.encode("utf-8", "replace").decode("utf-8")


def raw_header(msg: AnyMessage, name: str) -> list[str]:
    """Every value of one header as written, unfolded and unparsed."""
    return [unsurrogate(re.sub(r"\r?\n(?=[ \t])", "", v)) for k, v in msg.raw_items() if k.lower() == name.lower()]


def header_text(msg: AnyMessage, name: str) -> str | None:
    """One header of a parsed draft as text, or None. email.policy.default parses a header when
    it is read, so one it can't parse (an encoded line break, comments nested past the recursion
    limit) raises there: its raw value stands in."""
    try:
        v = msg.get(name)
    except Exception:
        raw = raw_header(msg, name)
        return raw[0] if raw else None
    return None if v is None else unsurrogate(str(v))


def header_chips(msg: AnyMessage, name: str) -> list[str]:
    """One address header of a parsed draft as composer chips, de-duplicated by address. A header
    that won't parse gives its bare addresses."""
    pairs: list[tuple[str, str]]
    try:
        pairs = [(a.display_name, a.addr_spec) for h in msg.get_all(name) or [] for a in h.addresses]
    except Exception:
        raw = raw_header(msg, name)
        try:
            pairs = [("", a) for _, a in email.utils.getaddresses(raw)]
        except Exception:                                # its comment parser recurses too
            pairs = []
        if not any("@" in a for _, a in pairs):          # or gives up with ('', ''): the addresses as written
            pairs = [("", a) for v in raw for a in re.findall(r'[^\s<>,;:"()\[\]]+@[^\s<>,;:"()\[\]]+', v)]
    out: list[str] = []
    seen: set[str] = set()
    for n, a in pairs:
        n, a = unsurrogate(n or ""), unsurrogate(a or "")
        if "@" not in a or a.lower() in seen:
            continue
        seen.add(a.lower())
        out.append(addr_chip(n, a))
    return out
