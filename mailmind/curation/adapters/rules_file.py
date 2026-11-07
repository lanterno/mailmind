"""The rule book's one reader and writer: mailmind_rules.json, in the data directory.

The file holds the keep and block lists and each sender's shelf life. It's read again only when it
changed; one that won't parse leaves the last rules read well, so a bad edit never stops a sync, and the
next save moves it to backups/ first. A save writes the whole file or nothing, and a change reads, changes and
saves as one step, so two at once don't lose either. That holds within this process.

`python -m mailmind rules` lists and changes them.
"""
import copy
import os
import json
import tempfile
import threading
from datetime import datetime, timezone

from mailmind.curation.domain import rulebook
from mailmind.curation.domain.rulebook import EXPIRE, KEYS
from mailmind.platform import settings
from mailmind.platform.logging import get_logger

log = get_logger("rules")

__all__ = ["EXPIRE", "KEYS", "add", "load", "remove", "save", "set_expiry", "sets"]

_lock = threading.Lock()
_changing = threading.Lock()   # one change at a time: each reads the whole file and writes the whole file back
_good = {}     # path -> (stamp, rules, sets): the last rules read well from that file
_bad = {}      # path -> the stamp of the copy that couldn't be read, so it's said once


def _stamp(path):
    st = os.stat(path)
    return st.st_mtime_ns, st.st_size, st.st_ino


def _parse(path):
    with open(path) as f:
        return rulebook.normalized(json.load(f))


def _read():
    """(rules, sets) for the rules file as it is now, read again only when it changed. A file that
    can't be read gives the last rules read well from it, or none, so a bad edit or a write cut
    short never stops the classifier, the sync or the routes."""
    path = settings.current().rules
    try:
        stamp = _stamp(path)
    except FileNotFoundError:
        return rulebook.empty(), rulebook.no_sets()
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
                log.warning("%(what)s can't be read; %(why)s until it can",
                            {"what": os.path.basename(path), "why": "keeping the rules last read" if good else "no rules apply"},
                            exc_info=e)
            return (good[1], good[2]) if good else (rulebook.empty(), rulebook.no_sets())
        sets = rulebook.as_sets(out)
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
    log.warning("the unreadable rules file is kept as backups/%(what)s", {"what": os.path.basename(aside)})


def save(rules):
    """Write the rules whole or not at all: a temp file beside the real one, owner-only, fsynced,
    then renamed over it."""
    data = rulebook.for_saving(rules)
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


def add(token, verdict):
    """verdict in {'keep','block'}. Routes a sender/domain into the right list
    and removes it from the opposite verdict to avoid contradictions."""
    token = rulebook.token_of(token)
    with _changing:
        rules = load()
        placed = rulebook.add(rules, token, verdict)
        save(rules)
    return token, placed


def remove(token):
    """Drop a keep/block entry. Leaves any expiry rule alone."""
    token = rulebook.token_of(token)
    with _changing:
        rules = load()
        rulebook.remove(rules, token)
        save(rules)


def set_expiry(token, hours):
    """Expire INBOX mail from `token` (sender or domain) after `hours`; 0 clears."""
    token = rulebook.token_of(token)
    hours = int(hours)
    with _changing:
        rules = load()
        rulebook.set_expiry(rules, token, hours)
        save(rules)
    return token, hours
