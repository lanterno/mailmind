"""The person's keep and block rules and each sender's shelf life, as data: what a rule covers and what a
change leaves. A domain entry also matches its sub-domains, and one matcher serves the classifier, the
Screener and expiry.

Expiry rules are separate from keep and block: a kept sender can also expire. Keep decides what the
classifier shows, expiry how long it stays.
"""
from collections.abc import Collection, Iterable, Iterator, Mapping
from typing import Any, Final, Literal, TypedDict, get_args

from mailmind.kernel.row import Row

type ListKey = Literal["keep_senders", "block_senders", "keep_domains", "block_domains"]
type RuleSets = Mapping[str, frozenset[str]]     # each of KEYS, as the classifier reads it

KEYS: tuple[ListKey, ...] = ("keep_senders", "block_senders", "keep_domains", "block_domains")
EXPIRE: Final = "expire_hours"   # {sender-or-domain: hours}
type Verdict = Literal["keep", "block"]                 # what a rule says of a sender or a domain
VERDICTS: tuple[Verdict, ...] = get_args(Verdict.__value__)
type RuleChange = Verdict | Literal["remove"]          # what a request may ask of one: a request's schema names it so
_LISTS: dict[tuple[str, bool], ListKey] = {("keep", False): "keep_senders", ("block", False): "block_senders",
                                           ("keep", True): "keep_domains", ("block", True): "block_domains"}


class Rules(TypedDict):
    keep_senders: list[str]
    block_senders: list[str]
    keep_domains: list[str]
    block_domains: list[str]
    expire_hours: dict[str, int]


def empty() -> Rules:
    return {"keep_senders": [], "block_senders": [], "keep_domains": [], "block_domains": [], "expire_hours": {}}


def no_sets() -> RuleSets:
    return {k: frozenset[str]() for k in KEYS}


def normalized(raw: Any) -> Rules:
    """The rules a file held, lower-cased, without repeats, and only shelf lives that are positive. Raises
    on a shape that isn't rules (ValueError, TypeError, AttributeError)."""
    out = empty()
    for k in KEYS:
        out[k] = list(dict.fromkeys(x.lower() for x in raw.get(k, [])))
    shelf_lives: dict[str, Any] = raw.get(EXPIRE) or {}
    out[EXPIRE] = {t.lower(): int(h) for t, h in shelf_lives.items() if int(h) > 0}
    return out


def as_sets(rules: Rules) -> RuleSets:
    """The keep and block lists as frozensets, what the classifier reads."""
    return {k: frozenset(rules[k]) for k in KEYS}


def for_saving(rules: Rules) -> Rules:
    data = empty()
    for k in KEYS:
        data[k] = sorted(set(rules.get(k, [])))
    data[EXPIRE] = dict(sorted((rules.get(EXPIRE) or {}).items()))
    return data


def token_of(raw: str) -> str:
    """A sender or a domain as a rule keeps it."""
    return raw.strip().lower()


def is_token(token: str) -> bool:
    """Whether a rule could name this: an address or a domain, and a bare word is neither."""
    return bool(token) and ("." in token or "@" in token)


def is_domain(token: str) -> bool:
    return "@" not in token and "." in token


def domain_of(addr: str) -> str:
    return addr.rpartition("@")[2]


def domain_covers(domain: str, entry: str) -> bool:
    """`domain` is `entry` or one of its sub-domains, and no lookalike. The one matcher."""
    return domain == entry or domain.endswith("." + entry)


def domain_listed(domain: str, listed: Iterable[str]) -> bool:
    """True if domain equals or is a sub-domain of any entry in `listed`."""
    return any(domain_covers(domain, d) for d in listed)


def decided(addr: str, domain: str, rules: Mapping[str, Collection[str]]) -> bool:
    """Whether a keep or block rule already covers this sender, by address or by domain."""
    return (addr in rules.get("keep_senders", []) or addr in rules.get("block_senders", [])
            or domain_listed(domain, rules.get("keep_domains", []))
            or domain_listed(domain, rules.get("block_domains", [])))


def undecided(senders: Iterable[Row], rules: Mapping[str, Collection[str]]) -> Iterator[Row]:
    """Those of `senders`, rows naming each by `addr`, that no keep or block rule covers yet: who is left to decide on."""
    return (r for r in senders if not decided(r["addr"], domain_of(r["addr"]), rules))


def keep_ruled(addr: str | None, rules: Mapping[str, Collection[str]]) -> bool:
    """A keep rule covers this sender, by address or by domain, whatever anything else made of its mail."""
    addr = (addr or "").lower()
    return addr in rules["keep_senders"] or domain_listed(addr.partition("@")[2], rules["keep_domains"])


def add(rules: Rules, token: str, verdict: Verdict) -> ListKey:
    """Put `token` under `verdict` ('keep' or 'block') and take it out of the opposite one, so a token is
    never in both. Returns the list it went in."""
    domain = is_domain(token)
    placed, other = _LISTS[verdict, domain], _LISTS["block" if verdict == "keep" else "keep", domain]
    rules[placed].append(token)
    rules[other] = [x for x in rules[other] if x != token]
    return placed


def remove(rules: Rules, token: str) -> None:
    """Drop a keep or block entry. Leaves any expiry rule alone: clear that with a shelf life of 0, so
    removing a keep chip can't silently stop expiry."""
    for k in KEYS:
        rules[k] = [x for x in rules[k] if x != token]


def set_expiry(rules: Rules, token: str, hours: int) -> None:
    """Expire INBOX mail from `token` (sender or domain) after `hours`; 0 clears."""
    if hours > 0:
        rules[EXPIRE][token] = hours
    else:
        rules[EXPIRE].pop(token, None)


def expiry_hours(addr: str | None, expire: Mapping[str, int]) -> int | None:
    """Hours after which mail from `addr` expires under the `expire` map, or None.
    An exact sender entry wins over a domain entry; a domain also matches sub-domains."""
    addr = (addr or "").lower()
    if addr in expire:
        return expire[addr]
    dom = addr.partition("@")[2]
    hits = [h for t, h in expire.items() if is_domain(t) and domain_covers(dom, t)]
    return min(hits) if hits else None
