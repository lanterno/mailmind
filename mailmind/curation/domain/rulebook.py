"""The person's keep and block rules and each sender's shelf life, as data: what a rule covers and what a
change leaves. A domain entry also matches its sub-domains, and one matcher serves the classifier, the
Screener and expiry.

Expiry rules are separate from keep and block: a kept sender can also expire. Keep decides what the
classifier shows, expiry how long it stays.
"""
KEYS = ("keep_senders", "block_senders", "keep_domains", "block_domains")
EXPIRE = "expire_hours"   # {sender-or-domain: hours}


def empty():
    return {**{k: [] for k in KEYS}, EXPIRE: {}}


def no_sets():
    return {k: frozenset() for k in KEYS}


def normalized(raw):
    """The rules a file held, lower-cased, without repeats, and only shelf lives that are positive. Raises
    on a shape that isn't rules (ValueError, TypeError, AttributeError)."""
    out: dict = {k: list(dict.fromkeys(x.lower() for x in raw.get(k, []))) for k in KEYS}
    out[EXPIRE] = {t.lower(): int(h) for t, h in (raw.get(EXPIRE) or {}).items() if int(h) > 0}
    return out


def as_sets(rules):
    """The keep and block lists as frozensets, what the classifier reads."""
    return {k: frozenset(rules[k]) for k in KEYS}


def for_saving(rules):
    data: dict = {k: sorted(set(rules.get(k, []))) for k in KEYS}
    data[EXPIRE] = dict(sorted((rules.get(EXPIRE) or {}).items()))
    return data


def is_domain(token):
    return "@" not in token and "." in token


def domain_listed(domain, listed):
    """True if domain equals or is a sub-domain of any entry in `listed`."""
    return any(domain == d or domain.endswith("." + d) for d in listed)


def decided(addr, domain, rules):
    """Whether a keep or block rule already covers this sender, by address or by domain."""
    return (addr in rules.get("keep_senders", []) or addr in rules.get("block_senders", [])
            or domain_listed(domain, rules.get("keep_domains", []))
            or domain_listed(domain, rules.get("block_domains", [])))


def add(rules, token, verdict):
    """Put `token` under `verdict` ('keep' or 'block') and take it out of the opposite one, so a token is
    never in both. Returns the list it went in."""
    skind = "domains" if is_domain(token) else "senders"
    rules[f"{verdict}_{skind}"].append(token)
    other = "block" if verdict == "keep" else "keep"
    rules[f"{other}_{skind}"] = [x for x in rules[f"{other}_{skind}"] if x != token]
    return f"{verdict}_{skind}"


def remove(rules, token):
    """Drop a keep or block entry. Leaves any expiry rule alone: clear that with a shelf life of 0, so
    removing a keep chip can't silently stop expiry."""
    for k in KEYS:
        rules[k] = [x for x in rules[k] if x != token]


def set_expiry(rules, token, hours):
    """Expire INBOX mail from `token` (sender or domain) after `hours`; 0 clears."""
    if hours > 0:
        rules[EXPIRE][token] = hours
    else:
        rules[EXPIRE].pop(token, None)


def expiry_hours(addr, expire):
    """Hours after which mail from `addr` expires under the `expire` map, or None.
    An exact sender entry wins over a domain entry; a domain also matches sub-domains."""
    addr = (addr or "").lower()
    if addr in expire:
        return expire[addr]
    dom = addr.partition("@")[2]
    hits = [h for t, h in expire.items() if is_domain(t) and (dom == t or dom.endswith("." + t))]
    return min(hits) if hits else None
