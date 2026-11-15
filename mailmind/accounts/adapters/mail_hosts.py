"""A domain's mail servers, as its MX records name them, asked of this computer's resolvers (application.ports.MailHosts).

Python's standard library looks a name up for its addresses alone, so this asks for the one record type itself: one
question over UDP, as RFC 1035 writes it, to each nameserver /etc/resolv.conf lists in turn, the resolvers the system's
own lookups ask, and the answer read with its names' compression followed. The Rust server's `mm_net::dns` asks and reads
alike. Nothing is cached.

What it finds only offers a preset a person then chooses, so an answer that never came, or a forged one, at worst offers
nothing, or offers the wrong preset: it sends nothing and chooses nothing. All the same an answer counts only from the
nameserver asked (the socket is connected to it), to the question asked (its id, drawn at random, and its question
match), and is read no further than it goes. A nameserver that is silent for TIMEOUT, or answers that it failed, is passed
over for the next, and at most MOST_ASKED are asked.
"""
import ipaddress
import secrets
import socket
import struct
import time

from mailmind.platform.db import txguard

RESOLV_CONF = "/etc/resolv.conf"
PORT = 53
TIMEOUT = 2.0               # seconds a nameserver has to answer
MOST_ASKED = 3              # nameservers asked, at most, as a resolver asks its own
MX = 15                     # the record type
IN = 1                      # the class: the Internet's
LONGEST_ANSWER = 4096       # bytes read of a datagram, more than one carries without EDNS
LONGEST_NAME = 253
MOST_JUMPS = 64             # compression pointers followed in one name: a loop of them ends there


class Failed(Exception):
    """The nameserver answered the question, saying it couldn't: the next one is asked."""


def system_nameservers() -> list[tuple[str, int]]:
    """The nameservers this computer's resolvers ask, as /etc/resolv.conf lists them; none when it can't be read."""
    try:
        with open(RESOLV_CONF, encoding="utf-8", errors="replace") as f:
            return nameservers_in(f.read())
    except OSError:
        return []


def nameservers_in(text: str) -> list[tuple[str, int]]:
    """Each `nameserver` line's address, in the file's order, on port 53. One that isn't an address, or names the network
    interface it is reached through (`fe80::1%en0`), is left out."""
    found: list[tuple[str, int]] = []
    for line in text.splitlines():
        words = line.split()
        if len(words) < 2 or words[0] != "nameserver" or "%" in words[1]:
            continue
        try:
            found.append((str(ipaddress.ip_address(words[1])), PORT))
        except ValueError:
            continue
    return found


def of(domain: str) -> list[str]:
    """The hosts `domain`'s MX records name, as the first nameserver to answer says: none when it has none, or when no
    nameserver answered."""
    txguard.refuse_network("a DNS lookup")
    for nameserver in system_nameservers()[:MOST_ASKED]:
        found = _ask(nameserver, domain)
        if found is not None:
            return found
    return []


def _ask(nameserver: tuple[str, int], domain: str) -> list[str] | None:
    """What `nameserver` answers of `domain`'s MX records, or None when it didn't, or said it couldn't."""
    ident = secrets.randbits(16)
    family = socket.AF_INET6 if ":" in nameserver[0] else socket.AF_INET
    try:
        question = query(ident, domain)
        with socket.socket(family, socket.SOCK_DGRAM) as s:
            s.settimeout(TIMEOUT)
            s.connect(nameserver)
            s.send(question)
            deadline = time.monotonic() + TIMEOUT
            while (left := deadline - time.monotonic()) > 0:
                s.settimeout(left)
                found = answer(ident, domain, s.recv(LONGEST_ANSWER))
                if found is not None:
                    return found
    except (OSError, ValueError, Failed):     # silent, unreachable or failed; a domain no question can ask about
        return None
    return None


def query(ident: int, domain: str) -> bytes:
    """The question of `domain`'s MX records, with the id `ident`, recursion desired. ValueError for a domain whose labels
    no question can carry."""
    labels = domain.split(".")
    if not all(0 < len(label) <= 63 and label.isascii() for label in labels) or len(domain) > LONGEST_NAME:
        raise ValueError("no domain a question can carry")
    name = b"".join(bytes([len(label)]) + label.encode("ascii") for label in labels) + b"\0"
    return struct.pack(">HHHHHH", ident, 0x0100, 1, 0, 0, 0) + name + struct.pack(">HH", MX, IN)


def answer(ident: int, domain: str, data: bytes) -> list[str] | None:
    """The hosts the answer to `query(ident, domain)` names, in its order: none when the domain has no MX record or doesn't
    exist. None when `data` isn't that answer, and Failed when it is, and says the nameserver couldn't."""
    if len(data) < 12:
        return None
    got, flags, questions, answers = struct.unpack_from(">HHHH", data)
    if got != ident or not flags & 0x8000 or questions != 1:
        return None
    try:
        asked, at = _name(data, 12)
        kind, klass = struct.unpack_from(">HH", data, at)
    except (ValueError, struct.error):
        return None
    if asked != domain.lower() or (kind, klass) != (MX, IN):
        return None
    code = flags & 0x000F
    if code == 3:                   # no such domain
        return []
    if code != 0:
        raise Failed()
    at += 4
    hosts: list[str] = []
    for _ in range(answers):
        try:
            _, at = _name(data, at)
            kind, klass, _ttl, length = struct.unpack_from(">HHIH", data, at)
            at += 10
            if at + length > len(data):
                break
            if (kind, klass) == (MX, IN) and length > 2:
                hosts.append(_name(data, at + 2)[0])          # after the preference, the host
            at += length
        except (ValueError, struct.error):
            break                                             # a record cut short, as a truncated answer's last is
    return hosts


def _name(data: bytes, at: int) -> tuple[str, int]:
    """The name at `at`, in lower case and without a final dot, and where what follows it starts. ValueError for one that
    runs past the data, loops, is longer than a name can be or isn't ASCII."""
    labels: list[str] = []
    after: int | None = None
    jumps = 0
    while True:
        if at >= len(data):
            raise ValueError("a name past the data")
        n = data[at]
        if n & 0xC0 == 0xC0:                                  # a pointer to the rest of the name, earlier in the data
            if at + 1 >= len(data) or jumps >= MOST_JUMPS:
                raise ValueError("a pointer past the data, or a loop")
            after = at + 2 if after is None else after
            jumps += 1
            at = ((n & 0x3F) << 8) | data[at + 1]
            continue
        if n & 0xC0:
            raise ValueError("a label of a kind no answer uses")
        at += 1
        if n == 0:
            break
        label = data[at:at + n]
        if len(label) < n:
            raise ValueError("a label past the data")
        labels.append(label.decode("ascii").lower())
        at += n
    name = ".".join(labels)
    if len(name) > LONGEST_NAME:
        raise ValueError("a name longer than one can be")
    return name, at if after is None else after
