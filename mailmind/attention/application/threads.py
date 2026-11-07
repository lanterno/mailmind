"""Which thread a request names."""
from collections.abc import Mapping
from typing import Any

from mailmind.attention.application.ports import Accounts, Messages
from mailmind.kernel.ids import AccountId, Pk, ThreadRef
from mailmind.kernel.parsed import required_int


class Threads:
    def __init__(self, messages: Messages, accounts: Accounts):
        self._messages = messages
        self._accounts = accounts

    def target(self, c: object, body: Mapping[str, Any]) -> tuple[AccountId, ThreadRef] | tuple[None, None]:
        """Resolve (account, thread_id) from {account, thread_id} or {pk}. Validates the account."""
        acct = body.get("account")
        tid = body.get("thread_id")
        if body.get("pk") is not None and not (acct and tid):
            row = self._messages.get(c, Pk(required_int(body["pk"])))
            if not row:
                return None, None
            acct = acct or row["account"]
            tid = tid or row["thread_id"]   # NO synthetic pk: fallback — a pk-keyed mute would never
            #   match the real thread_id and would not survive reconcile/reset_folder (the durable-key
            #   invariant). Threading gives every visible message a real id, so this is safe.
        if not acct or acct not in set(self._accounts.emails()) or not tid:
            return None, None
        return acct, tid
