"""Optional persistent IMAP connection pool for the READ hot path (cold body fetch ~37s on
Proton → ~2s warm). Default OFF: with MAILMIND_POOL unset and no account asking, borrow() is byte-for-byte today's
behavior (fresh connect + logout per op), so landing this changes nothing until explicitly enabled.

A borrow yields a MailSession (mailmind.mailbox.application.session), opened through
mailmind.bootstrap.mailbox like any other. What is kept and when it is replaced is
mailmind.platform.workers.account_worker's. This says which accounts are pooled, that a NOOP is the
check and a LOGOUT the close, and that each account's provider gives the numbers.

SAFETY (load-bearing):
  • Only read-only paths (ingest.fetch_body, the folder rail's counts, a sweep's plan) borrow.
    MUTATIONS NEVER touch the pool — they keep opening a fresh session — so a pooled session can
    never carry a stale selected folder into a UID STORE/MOVE/EXPUNGE. All mutation invariants
    (UIDVALIDITY, never-bulk-EXPUNGE, etc.) are untouched.
  • Validate-on-take: a cached connection is NOOP'd before reuse; on any failure / age / use-count
    limit it is discarded and a guaranteed-fresh connection is opened. So an idle server-side drop is
    transparent. A mid-borrow error discards the slot and propagates exactly as a fresh connection
    would today.
  • One connection per account (slot), guarded by a per-account lock taken INSIDE the caller's
    IMAP gate (mailmind.platform.workers.gates; canonical order → no new deadlock). imaplib is not
    thread-safe per connection; the lock guarantees a pooled connection is never shared concurrently.
  • Proton Bridge (loopback/STARTTLS) has a low per-account connection cap; one pooled conn per
    account satisfies it, and we validate every borrow. Enable per-account, by the env list or its own "pool" key.

Enable:  MAILMIND_POOL=1            → all accounts
         MAILMIND_POOL=a@x.com,b@y → only those accounts (Phase-1: one Gmail)
         "pool": true on an account in mailmind.config.json, for when MAILMIND_POOL is unset: it lasts between runs
"""
import contextlib
from collections.abc import Generator
from typing import Any

from mailmind.mailbox.application.ports import Account, Accounts, OpenSession
from mailmind.mailbox.application.session import MailSession
from mailmind.platform import environment
from mailmind.platform.workers.account_worker import ConnectionPolicy, KeptConnections


def _logout(M: MailSession) -> None:
    try:
        M.logout()
    except Exception:
        pass


def _valid(M: MailSession) -> bool:
    """A cached session is reusable only if it answers NOOP OK."""
    try:
        return M.noop()
    except Exception:
        return False


class Pool(KeptConnections[MailSession]):
    def __init__(self, accounts: Accounts, open_session: OpenSession):
        super().__init__(_valid, _logout)
        self._accounts = accounts
        self._open_session = open_session

    def _enabled(self, acct: Account) -> bool:
        if environment.POOL.given():                         # the variable, when set, decides for this run
            which = environment.POOL.value()
            if which.every:
                return not self._accounts.profile(acct).bridge   # "all" = every account EXCEPT the fragile Bridge
            return acct["email"] in which.listed                 # explicit list honored verbatim
        return bool(acct.get("pool"))                        # and the file for every other: an account asks, by name


    def _policy(self, acct: Account) -> ConnectionPolicy:
        """ProviderProfile.recycle_seconds (600, well under a server's ~29-minute idle drop; 90 on
        Bridge) and recycle_uses (200; 40 on Bridge): Proton Bridge is fragile + connection-capped, so
        a pooled Bridge connection is recycled far more aggressively."""
        p = self._accounts.profile(acct)
        return ConnectionPolicy(p.recycle_seconds, p.recycle_uses)


    @contextlib.contextmanager
    def borrow(self, acct: Account) -> Generator[MailSession]:
        """Yield a MailSession for a READ-ONLY operation. Dormant (not asked for) → a fresh session, logged
        out after. Enabled → a validated pooled session that is repooled on a clean exit and discarded
        on any error."""
        if not self._enabled(acct):
            fresh = self._open_session(acct)
            try:
                yield fresh
            finally:
                _logout(fresh)
            return
        with self.lend(acct["email"], self._policy(acct), lambda: self._open_session(acct)) as pooled:
            yield pooled


    def stats(self) -> dict[str, Any]:
        """How the pool stands, in counts: whether it serves every account or a list of them, never which, and how each
        connection kept stands, with no account named."""
        if environment.POOL.given():
            which = environment.POOL.value()
            target = "all" if which.every else "listed" if which.listed else ""
        else:
            target = "listed" if any(a.get("pool") for a in self._accounts.accounts()) else ""
        return {"enabled": bool(target), "target": target, "counters": self.counters(), "slots": list(self.slots().values())}
