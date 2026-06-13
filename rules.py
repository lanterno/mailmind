"""Read/write the learned allow- & block-list that mailclass consults first.

The classifier (mailclass) READS mailmind_rules.json; this module is the small
API the driver uses to GROW it as the user makes keep/block decisions, so we
never re-litigate the same sender. A domain entry also matches its sub-domains.

  python3 rules.py list
  python3 rules.py keep  <sender@x | domain.com>
  python3 rules.py block <sender@x | domain.com>
  python3 rules.py remove <sender@x | domain.com>
"""
import os
import sys
import json

FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mailmind_rules.json")
KEYS = ("keep_senders", "block_senders", "keep_domains", "block_domains")


def load():
    if not os.path.exists(FILE):
        return {k: [] for k in KEYS}
    with open(FILE) as f:
        raw = json.load(f)
    return {k: list(dict.fromkeys(x.lower() for x in raw.get(k, []))) for k in KEYS}


def save(rules):
    with open(FILE, "w") as f:
        json.dump({k: sorted(set(rules.get(k, []))) for k in KEYS}, f, indent=2)


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
    token = token.strip().lower()
    rules = load()
    for k in KEYS:
        rules[k] = [x for x in rules[k] if x != token]
    save(rules)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        r = load()
        for k in KEYS:
            print(f"{k} ({len(r[k])}): {', '.join(r[k]) or '—'}")
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
