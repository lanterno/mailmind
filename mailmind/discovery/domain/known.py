"""Who counts as known, for the composer's "new to you" marks. Known: non-junk mail from them, synced non-draft mail
you sent them, a delivered outbox envelope (Bcc included), or a keep_senders rule. And who a new address reads as a slip of
the fingers for: someone you've written to, one edit away."""
import collections
import dataclasses
import json
from collections.abc import Callable, Container, Iterable

from mailmind.kernel import addresses
from mailmind.kernel.parsed import is_list
from mailmind.kernel.row import Row

MIN_HISTORY = 50     # fewer non-junk messages than this → too little history to call anyone new
MIN_NAME = 3         # the name before the @ this long at least: ed and al are two people, not one typo


@dataclasses.dataclass(frozen=True)
class Facts:
    """What the stored mail says about the addresses asked after. Holds no subject and no date."""
    accounts: list[str]                 # the accounts that hold mail
    senders: list[tuple[str, str]]      # (address, folder): non-junk mail from one of them
    synced: list[tuple[str, str]]       # (account, folder): every folder synced
    draft_folders: set[str]             # where a draft is kept
    copies: list[Row]                   # rows of mail to one of them: addr, account, folder, is_junk, sender
    envelopes: list[str | None]         # what the outbox delivered, as JSON lists
    thin: bool                          # too little history to call anyone new


@dataclasses.dataclass(frozen=True)
class Asked:
    """Addresses asked after, as they can be looked for."""
    keys: list[str]                     # each one's own key in the answer
    bad: list[str]                      # what no mail could go to
    want: dict[str, set[str]]           # each form to look for, and the keys it answers


def normal(raw: object) -> str | None:
    """The address mail to `raw` would actually go to, by compose's own parser, so a display
    name holding someone else's address can't stand in for it. None unless exactly one."""
    try:
        pairs = addresses.parse_addr_list([str(raw or "")[:320]])
    except ValueError:
        return None
    if len(pairs) != 1:
        return None
    a = pairs[0][1].lower()
    return a if len(a) <= 254 else None


def forms(a: str) -> set[str]:
    """a plus its IDNA twin: the outbox envelope stores punycode domains, received mail may not."""
    local, dom = a.split("@")
    out = {a}
    try:
        out.add(f"{local}@{addresses.idna_domain(dom).lower()}")
    except ValueError:
        pass
    if "xn--" in dom:
        try:
            out.add(f"{local}@{dom.encode('ascii').decode('idna').lower()}")
        except UnicodeError:
            pass
    return out


def asked(addrs: object) -> Asked:
    """What a request asks after. Raises ValueError for one that isn't a list of what a message could hold."""
    if not is_list(addrs):
        raise ValueError("addrs must be a list")
    if len(addrs) > addresses.MAX_RECIPIENTS:
        raise ValueError("too many addresses")
    keys: list[str] = []
    bad: list[str] = []
    want: dict[str, set[str]] = {}
    for raw in addrs:
        key = str(raw or "").strip().lower()[:320]
        a = normal(raw)
        if a is None:
            bad.append(key[:200])
            continue
        keys.append(key)
        for f in forms(a):
            want.setdefault(f, set()).add(key)
    return Asked(keys, bad, want)


def untagged(a: str) -> str:
    """`you+tag@host` as `you@host`, the mailbox a plus-address lands in."""
    local, at, domain = a.rpartition("@")
    return f"{local.partition('+')[0]}@{domain}" if at and "+" in local[1:] else a


def own_addresses(emails: Iterable[str], aliases: Iterable[str], holding: Iterable[str]) -> set[str]:
    """Your addresses, as the dashboard's state.myAddrs sees them: the accounts' and those that hold mail here. An account
    that is itself tagged has its plain mailbox along with it."""
    return {f for group in (emails, aliases, holding) for e in group for f in (e.lower(), untagged(e.lower()))}


def is_own(a: str, own: Container[str]) -> bool:
    """Yours, however it is tagged: you+tag@ is the mailbox you@ names."""
    return a in own or untagged(a) in own


def sent_from(facts: Facts, role_by_name: Callable[[str], str]) -> set[str]:
    """The addresses asked after that mail in a Sent folder went out from: send-as identities no account names."""
    return {addr for addr, folder in facts.senders if role_by_name(folder) == "sent"}


def verdicts(question: Asked, own: Container[str], hit: Container[str]) -> dict[str, dict[str, bool | str]]:
    """The answer for each address asked after, by any of its forms. Your own counts as known, and says it's yours."""
    items: dict[str, dict[str, bool | str]] = {k: {"known": False, "self": False} for k in question.keys}
    for form, keys in question.want.items():
        for k in keys:
            if is_own(form, own):
                items[k]["self"] = items[k]["known"] = True
            elif form in hit:
                items[k]["known"] = True
    return items


def sent_folders(facts: Facts, role_by_name: Callable[[str], str]) -> set[tuple[str, str]]:
    """(account, folder) of each synced folder that plays Sent."""
    return {pair for pair in facts.synced if role_by_name(pair[1]) == "sent"}


def _written_by_you(r: Row, facts: Facts, sent: Container[tuple[str, str]], own: Container[str],
                    role_by_name: Callable[[str], str]) -> bool:
    """Whether a stored copy of a message says you wrote it."""
    if r["folder"] in facts.draft_folders:         # the autosaved copy of the message being written must not vouch for its own recipient
        return False
    yours = (is_own(r["sender"] or "", own) and not r["is_junk"] and "draft" not in (r["folder"] or "").lower()
             and role_by_name(r["folder"]) != "junk")   # spam forging your From: vouches for no one
    return yours or (r["account"], r["folder"]) in sent  # a Sent folder: send-as identities that aren't accounts


def vouched(facts: Facts, wanted: set[str], own: Container[str], role_by_name: Callable[[str], str]) -> set[str]:
    """Which of the addresses `wanted` the stored mail vouches for. `own` is your addresses."""
    # junk excluded: one phishing mail can't vouch, whether we or the provider filed it
    hit = {addr for addr, folder in facts.senders if role_by_name(folder) != "junk"}
    sent = sent_folders(facts, role_by_name)
    hit |= {r["addr"] for r in facts.copies if _written_by_you(r, facts, sent, own, role_by_name)}
    for env in facts.envelopes:
        try:                               # queued rows may still be undone, or be the typo
            hit |= {str(x).lower() for x in json.loads(env or "[]")} & wanted
        except (ValueError, TypeError):
            continue
    return hit


def written_to(facts: Facts, copies: Iterable[Row], own: Container[str], role_by_name: Callable[[str], str]) -> list[str]:
    """Everyone the stored mail and the outbox say you've written to, the most written-to first, you left out. `copies` are
    the rows of who stored mail went to: addr, account, folder, is_junk, sender, and n, how many."""
    sent = sent_folders(facts, role_by_name)
    counts: collections.Counter[str] = collections.Counter()
    for r in copies:
        if _written_by_you(r, facts, sent, own, role_by_name):
            counts[r["addr"]] += r["n"]
    for env in facts.envelopes:
        try:
            counts.update(str(x).lower() for x in json.loads(env or "[]"))
        except (ValueError, TypeError):
            continue
    return [a for a, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if not is_own(a, own)]


def one_slip(a: str, b: str) -> bool:
    """Two strings one slip of the fingers apart: a letter added, dropped or changed, or two neighbours swapped. Numbers
    that differ are siblings (room3, room4), not slips."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    i = next((n for n, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    if len(a) != len(b):
        longer, shorter = (a, b) if len(a) > len(b) else (b, a)
        return longer[i + 1:] == shorter[i:]
    if a[i].isdigit() and b[i].isdigit():
        return False
    return a[i + 1:] == b[i + 1:] or (a[i] == b[i + 1:i + 2] and b[i] == a[i + 1:i + 2] and a[i + 2:] == b[i + 2:])


def reads_as_slip(a: str, person: str) -> bool:
    """Whether `a` reads as `person` mistyped: one slip in the name or in the domain, never in both. Two tags of one mailbox
    are no slip of each other, and a name too short to tell one person from another is left alone."""
    name, _, domain = untagged(a).rpartition("@")
    their_name, _, their_domain = untagged(person).rpartition("@")
    if domain == their_domain:
        return min(len(name), len(their_name)) >= MIN_NAME and one_slip(name, their_name)
    return name == their_name and one_slip(domain, their_domain)


def mark_near_misses(question: Asked, items: dict[str, dict[str, bool | str]], people: list[str]) -> None:
    """Add `near`, the person a new address reads as a slip of, to each of `items` that has one. `people` come most
    written-to first, so the likeliest wins, and a person's own address is never its own slip."""
    for form, keys in question.want.items():
        new = [k for k in keys if not items[k]["known"]]
        who = next((p for p in people if reads_as_slip(form, p)), None) if new else None
        for k in new:
            if who:
                items[k].setdefault("near", who)
