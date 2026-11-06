"""The per-account counts the page shows, from what is stored: no server is asked, and the cache only saves the sums."""
import time

from mailmind.kernel.when import now_iso
from mailmind.mailbox.application.ports import Accounts, Classifier


def _strip(p):
    p = dict(p)
    p.pop("_rows", None)
    return p


class LiveStats:
    TTL = 90                            # seconds to cache the live per-account stats

    def __init__(self, accounts: Accounts, curation: Classifier):
        self._accounts = accounts
        self._curation = curation
        self._cache = {"data": None, "exp": 0}

    def live(self, account=None, refresh=False):
        is_all = account is None or account == "all"
        if not refresh and is_all and self._cache["data"] and self._cache["exp"] > time.time():
            return self._cache["data"]
        accts = self._accounts.accounts() if is_all else [self._accounts.get(account)]
        out = []
        for a in accts:
            p = _strip(self._curation.plan_stored(a))    # DB-sourced (no IMAP / no IMAP gate) → instant;
            p["alias"] = a.get("alias")              # the Sweep buttons still re-verify live before moving
            out.append(p)
        data = {
            "accounts": out,
            "buckets": self._curation.buckets(),
            "totals": {
                "in_inbox": sum(a["in_inbox"] for a in out),
                "junk": sum(a["junk"] for a in out),
                "keep": sum(a["keep"] for a in out),
            },
            "generated": now_iso(),
        }
        if account is None or account == "all":
            self._cache["data"], self._cache["exp"] = data, time.time() + self.TTL
        return data


    def invalidate(self):
        self._cache["data"], self._cache["exp"] = None, 0
