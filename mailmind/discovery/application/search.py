"""Finding mail by what a person types, and what a bulk command's filter may reach. Local SQL: it never touches IMAP."""
from collections.abc import Mapping
from typing import Any

from mailmind.discovery.application.ports import AccountNames, SearchIndex
from mailmind.discovery.domain import guard, search
from mailmind.discovery.domain.query import QuerySpec
from mailmind.kernel.row import Row
from mailmind.kernel.when import now_iso, rel_time


class Search:
    def __init__(self, index: SearchIndex, accounts: AccountNames):
        self._index = index
        self._accounts = accounts

    def answer(self, qs: Mapping[str, list[str]]) -> dict[str, Any]:
        """Local FTS5 + operator search across synced mail."""
        want = search.asked(qs)
        if want is None:
            return search.nothing_asked()
        aliases = self._accounts.aliases()
        rows = self._index.search(want.spec, want.account, want.limit, want.offset)
        if rows is None:
            return search.bad_query()
        return search.page(rows, want, aliases, rel_time, now_iso())

    def for_asking(self, match: str, accounts: str | list[str], limit: int) -> list[Row] | None:
        """What a question to Ask reads, or None on a bad query. Read-only; see the index."""
        return self._index.for_asking(match, accounts, limit)

    def reach(self, spec: QuerySpec, cap: int, apart: set[str],
              sample_size: int) -> tuple[int | None, list[tuple[int, str]], list[dict[str, str]], int]:
        """Exact COUNT + a capped set of (message key, folder) + a sample for a validated filter, leaving out the
        folders that play the roles in `apart`. Read-only; the messages come from OUR db, never the model.

        `matched` is how many rows the filter says, whatever it may reach, for the blast-radius check. Each message
        counts once: its copy in the Inbox, else any but the one in All Mail. Returns (count, messages, sample,
        matched) or (None, [], [], 0) on a bad query."""
        found = self._index.reach(spec, cap, apart)
        if found is None:
            return None, [], [], 0
        count, rows, matched = found
        messages, sample = search.reached(rows, sample_size, rel_time)
        return count, messages, sample, matched

    def sweeps_whole_mailbox(self, matched: int) -> bool:
        return guard.sweeps_whole_mailbox(matched, self._index.total())
