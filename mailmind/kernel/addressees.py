"""Whom a stored message went to, as a row of a list names them: the first people its To and Cc name, and how many more.

The store keeps To and Cc as one decoded text with a space between the two (`mailbox.domain.ingested.recipients_text`), which
no header's parser reads whole: two bare addresses run together, and a name decoded from an encoded word may hold a comma no
quote guards. So this reads it a mailbox at a time: a name's words, then an address in angle brackets, or a bare address
standing alone, and a comma between a name's words keeps them one name ("Acme, Inc."). The Rust core's
`mm_kernel::addressees` reads it the same, held to the same cases (tests/fixtures/addressees.json)."""
from typing import Any

FIRST = 2       # the people a row names; the rest it counts

_ENDS_A_WORD = '"(<,;:'


def _tokens(text: str) -> list[tuple[str, str]]:
    """The text as a header's parts: a word, a quoted string, an address in angle brackets, or a comma, a semicolon or a
    colon. A comment goes, and a quote or a bracket left open runs to the end."""
    out: list[tuple[str, str]] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c == '"':
            i += 1
            quoted: list[str] = []
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    i += 1
                quoted.append(text[i])
                i += 1
            out.append(("quoted", "".join(quoted)))
            i += 1
        elif c == "(":
            depth = 0
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == "(":
                    depth += 1
                elif text[i] == ")":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
        elif c == "<":
            end = text.find(">", i + 1)
            end = n if end < 0 else end
            out.append(("angle", text[i + 1:end]))
            i = end + 1
        elif c in ",;:":
            out.append((c, c))
            i += 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in _ENDS_A_WORD:
                j += 1
            out.append(("word", text[i:j]))
            i = j
    return out


def people(text: str | None) -> list[tuple[str | None, str]]:
    """Each address To and Cc name, once, in their order, with the name it was written with, or None."""
    found: list[tuple[str | None, str]] = []
    seen: set[str] = set()
    words: list[str] = []

    def add(addr: str) -> None:
        addr = addr.strip()
        if "@" in addr and addr.lower() not in seen:
            seen.add(addr.lower())
            found.append((" ".join(w for w in (w.strip() for w in words) if w) or None, addr))

    tokens = _tokens(text or "")
    for at, (kind, value) in enumerate(tokens):
        if kind == "angle":
            add(value)
            words = []
        elif kind == "word" and "@" in value and not (at + 1 < len(tokens) and tokens[at + 1][0] == "angle"):
            words = []
            add(value)
        elif kind in ("word", "quoted"):
            words.append(value)
        elif kind == ",":
            if words:
                words[-1] += ","        # a name's comma, when no address came before it: "Acme, Inc. <billing@...>"
        else:
            words = []                  # a group's name, or its end
    return found


def listed(text: str | None) -> dict[str, Any]:
    """What a row carries of whom its message went to: the first `FIRST`, each with the name it was written with, and how
    many more."""
    found = people(text)
    return {"sent_to": [{"name": name, "addr": addr} for name, addr in found[:FIRST]],
            "sent_to_more": max(0, len(found) - FIRST)}
