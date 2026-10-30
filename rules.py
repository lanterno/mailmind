"""Read/write the learned allow- & block-list that mailclass consults first.

The classifier (mailclass) READS mailmind_rules.json; this module is the small
API the driver uses to GROW it as the user makes keep/block decisions, so we
never re-litigate the same sender. A domain entry also matches its sub-domains.

Expiry rules are separate from keep/block: `expire github.com 72` moves INBOX mail
from that sender/domain to Trash once it is older than 72 hours (manage.py expire,
and the server's expire_loop). A kept sender can also expire — keep decides what
the classifier shows, expiry decides how long it stays.

  python3 rules.py list
  python3 rules.py keep  <sender@x | domain.com>
  python3 rules.py block <sender@x | domain.com>
  python3 rules.py remove <sender@x | domain.com>
  python3 rules.py expire <sender@x | domain.com> <hours>   # 0 clears
"""
import os
import sys
import json

FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mailmind_rules.json")
KEYS = ("keep_senders", "block_senders", "keep_domains", "block_domains")
EXPIRE = "expire_hours"   # {sender-or-domain: hours}


def load():
    if not os.path.exists(FILE):
        return {**{k: [] for k in KEYS}, EXPIRE: {}}
    with open(FILE) as f:
        raw = json.load(f)
    out: dict = {k: list(dict.fromkeys(x.lower() for x in raw.get(k, []))) for k in KEYS}
    out[EXPIRE] = {t.lower(): int(h) for t, h in (raw.get(EXPIRE) or {}).items() if int(h) > 0}
    return out


def save(rules):
    data: dict = {k: sorted(set(rules.get(k, []))) for k in KEYS}
    data[EXPIRE] = dict(sorted((rules.get(EXPIRE) or {}).items()))
    with open(FILE, "w") as f:
        json.dump(data, f, indent=2)


def _is_domain(token):
    return "@" not in token and "." in token


def add(token, verdict):
    """verdict in {'keep','block'}. Routes a sender/domain into the right list
    and removes it from the opposite verdict to avoid contradictions."""
    token = token.strip().lower()
    rules = load()
    skind = "domains" if _is_domain(token) else "senders"
    rules[f"{verdict}_{skind}"].append(token)
    other = "block" if verdict == "keep" else "keep"
    rules[f"{other}_{skind}"] = [x for x in rules[f"{other}_{skind}"] if x != token]
    save(rules)
    return token, f"{verdict}_{skind}"


def remove(token):
    """Drop a keep/block entry. Leaves any expiry rule alone — clear that with
    set_expiry(token, 0), so removing a keep chip in the UI can't silently stop expiry."""
    token = token.strip().lower()
    rules = load()
    for k in KEYS:
        rules[k] = [x for x in rules[k] if x != token]
    save(rules)


def set_expiry(token, hours):
    """Expire INBOX mail from `token` (sender or domain) after `hours`; 0 clears."""
    token = token.strip().lower()
    hours = int(hours)
    rules = load()
    if hours > 0:
        rules[EXPIRE][token] = hours
    else:
        rules[EXPIRE].pop(token, None)
    save(rules)
    return token, hours


def expiry_hours(addr, expire):
    """Hours after which mail from `addr` expires under the `expire` map, or None.
    An exact sender entry wins over a domain entry; a domain also matches sub-domains."""
    addr = (addr or "").lower()
    if addr in expire:
        return expire[addr]
    dom = addr.partition("@")[2]
    hits = [h for t, h in expire.items() if _is_domain(t) and (dom == t or dom.endswith("." + t))]
    return min(hits) if hits else None


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        r = load()
        for k in KEYS:
            print(f"{k} ({len(r[k])}): {', '.join(r[k]) or '—'}")
        print(f"{EXPIRE} ({len(r[EXPIRE])}): "
              f"{', '.join(f'{t} {h}h' for t, h in r[EXPIRE].items()) or '—'}")
    elif cmd == "expire" and len(sys.argv) == 4:
        t, h = set_expiry(sys.argv[2], sys.argv[3])
        print(f"  {t} -> {'expires after ' + str(h) + 'h' if h else 'no expiry'}")
    elif cmd in ("keep", "block") and len(sys.argv) > 2:
        for tok in sys.argv[2:]:
            t, where = add(tok, cmd)
            print(f"  {t} -> {where}")
    elif cmd == "remove" and len(sys.argv) > 2:
        for tok in sys.argv[2:]:
            remove(tok)
            print(f"  removed {tok}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
