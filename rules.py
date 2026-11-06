"""Read/write the learned allow- & block-list that mailclass consults first.

The classifier (mailclass) READS mailmind_rules.json; this module is the small
API the driver uses to GROW it as the user makes keep/block decisions, so we
never re-litigate the same sender. A domain entry also matches its sub-domains.

Expiry rules are separate from keep/block: `expire github.com 72` moves INBOX mail
from that sender/domain to Trash once it is older than 72 hours (`python -m mailmind expire`,
and the server's expire job). A kept sender can also expire — keep decides what
the classifier shows, expiry decides how long it stays.

`python -m mailmind rules` lists and changes them.
"""
import copy
import os
import json
import tempfile
import threading
from datetime import datetime, timezone

from mailmind.platform import settings

KEYS = ("keep_senders", "block_senders", "keep_domains", "block_domains")
EXPIRE = "expire_hours"   # {sender-or-domain: hours}

_lock = threading.Lock()
_good = {}     # path -> (stamp, rules, sets): the last rules read well from that file
_bad = {}      # path -> the stamp of the copy that couldn't be read, so it's said once


def _empty():
    return {**{k: [] for k in KEYS}, EXPIRE: {}}


def _stamp(path):
    st = os.stat(path)
    return st.st_mtime_ns, st.st_size, st.st_ino


def _parse(path):
    with open(path) as f:
        raw = json.load(f)
    out: dict = {k: list(dict.fromkeys(x.lower() for x in raw.get(k, []))) for k in KEYS}
    out[EXPIRE] = {t.lower(): int(h) for t, h in (raw.get(EXPIRE) or {}).items() if int(h) > 0}
    return out


def _read():
    """(rules, sets) for the rules file as it is now, read again only when it changed. A file that
    can't be read gives the last rules read well from it, or none, so a bad edit or a write cut
    short never stops the classifier, the sync or the routes."""
    path = settings.current().rules
    try:
        stamp = _stamp(path)
    except FileNotFoundError:
        return _empty(), {k: frozenset() for k in KEYS}
    except OSError:
        stamp = None
    with _lock:
        good = _good.get(path)
        if good is not None and stamp is not None and good[0] == stamp:
            return good[1], good[2]
        try:
            if stamp is None:
                raise OSError("can't stat")
            out = _parse(path)
        except (OSError, ValueError, TypeError, AttributeError) as e:
            if _bad.get(path) != stamp:
                _bad[path] = stamp
                print(f"[rules] {os.path.basename(path)} can't be read ({type(e).__name__}); "
                      f"{'keeping the rules last read' if good else 'no rules apply'} until it can",
                      flush=True)
            return (good[1], good[2]) if good else (_empty(), {k: frozenset() for k in KEYS})
        sets = {k: frozenset(out[k]) for k in KEYS}
        _good[path] = (stamp, out, sets)
        _bad.pop(path, None)
        return out, sets


def load():
    """The rules, as lists (and expiry hours) of the caller's own to change."""
    return copy.deepcopy(_read()[0])


def sets():
    """The keep and block lists as frozensets, for the classifier: shared, never changed."""
    return _read()[1]


def _set_aside(path):
    """A rules file that doesn't parse is kept in backups/ before a save replaces it, so a bad
    hand edit loses nothing."""
    try:
        _parse(path)
        return
    except FileNotFoundError:
        return
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    keep = os.path.join(os.path.dirname(path), "backups")
    os.makedirs(keep, mode=0o700, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    aside = os.path.join(keep, f"mailmind_rules-unreadable-{stamp}.json")
    os.replace(path, aside)
    os.chmod(aside, 0o600)
    print(f"[rules] the unreadable rules file is kept as backups/{os.path.basename(aside)}", flush=True)


def save(rules):
    """Write the rules whole or not at all: a temp file beside the real one, owner-only, fsynced,
    then renamed over it."""
    data: dict = {k: sorted(set(rules.get(k, []))) for k in KEYS}
    data[EXPIRE] = dict(sorted((rules.get(EXPIRE) or {}).items()))
    path = settings.current().rules
    _set_aside(path)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".rules-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


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
