"""Finding mail by what a person types, and what a bulk command's filter may reach. Local SQL: it never touches IMAP."""
from mailmind.discovery.domain import guard
from mailmind.discovery.domain.query import QuerySpec
from mailmind.kernel import attention
from mailmind.kernel.when import now_iso, rel_time


class Search:
    def __init__(self, index, accounts):
        self._index = index
        self._accounts = accounts

    def answer(self, qs):
        """Local FTS5 + operator search across synced mail."""
        q = (qs.get("q", [""])[0] or "").strip()
        if not q:
            return {"ok": True, "query": "", "items": [], "total": 0, "has_more": False}
        scope = qs.get("scope", ["account"])[0]
        account = qs.get("account", [None])[0]
        sort = qs.get("sort", ["rank"])[0]
        limit = max(1, min(200, int(qs.get("limit", ["80"])[0])))
        offset = max(0, int(qs.get("offset", ["0"])[0]))
        amap = self._accounts.aliases()
        rows = self._index.search(QuerySpec.parse(q), None if scope == "all" else account, sort, limit, offset)
        if rows is None:
            return {"ok": False, "error": "bad query"}
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [{
            "pk": r["pk"], "key": r["message_key"], "account": r["account"], "folder": r["folder"],
            "alias": amap.get(r["account"]),
            "from_name": r["from_name"] or r["from_addr"], "from_addr": r["from_addr"],
            "subject": r["subject"] or "(no subject)", "when": rel_time(r["date_iso"]),
            "date": (r["date_iso"] or "")[:10],
            "snippet": (r["snippet"] or "")[:200], "seen": bool(r["seen"]), "flagged": bool(r["flagged"]),
            "has_attach": bool(r["has_attach"]), "is_junk": bool(r["is_junk"]), "bucket": r["bucket"],
            "importance": r["importance"], **attention.answers(r["importance"], r["category"], r["action"]),
        } for r in rows]
        return {"ok": True, "query": q, "scope": scope, "items": items,
                "offset": offset, "has_more": has_more, "generated": now_iso()}

    def reach(self, spec, cap, apart, sample_size):
        """Exact COUNT + a capped set of (message key, folder) + a sample for a validated filter, leaving out the
        folders that play the roles in `apart`. Read-only; the messages come from OUR db, never the model.

        `matched` is how many rows the filter says, whatever it may reach, for the blast-radius check. Each message
        counts once: its copy in the Inbox, else any but the one in All Mail. Returns (count, messages, sample,
        matched) or (None, [], [], 0) on a bad query."""
        found = self._index.reach(spec, cap, apart)
        if found is None:
            return None, [], [], 0
        count, rows, matched = found
        messages = [(r["message_key"], r["folder"]) for r in rows]
        sample = [{"from": (r["from_name"] or r["from_addr"] or ""),
                   "subject": r["subject"] or "(no subject)",
                   "when": rel_time(r["date_iso"])} for r in rows[:sample_size]]
        return count, messages, sample, matched

    def sweeps_whole_mailbox(self, matched):
        return guard.sweeps_whole_mailbox(matched, self._index.total())
