"""IMAP IDLE for MailMind (stdlib only): one worker per account watches its INBOX, and a
dispatcher coalesces a burst of changes into one InboxChanged on mailmind.platform.bus, which the
server answers with a sync of those accounts. The open tabs hear of the result through the live
event stream, mailmind.platform.sse.

The workers follow the configuration (mailmind.platform.workers.idle_supervisor holds how): an
account added gets a worker, one removed or disabled loses its own, and one now reached another way
(host, port, TLS, credential) gets a fresh worker in place of the old. The account events
(mailmind.accounts.domain.events) call reconcile(), and a worker that finds its account changed
under it leaves on its own.

Bounded (1 IDLE conn/account, capped accounts), reconnect-safe, daemon-thread clean exit.
"""
import threading
from collections.abc import Mapping
from typing import Any

from mailmind.mailbox.application.ports import Account, Accounts, OpenSession
from mailmind.mailbox.application.session import MailSession
from mailmind.mailbox.domain.events import InboxChanged
from mailmind.platform import bus
from mailmind.platform.logging import get_logger
from mailmind.platform.workers.idle_supervisor import IdleSupervisor, Watch

log = get_logger("idle")
_ENDPOINT = ("kind", "host", "port", "starttls", "credential", "strip_spaces",   # how it's reached
             "imap_adapter")


def _same_endpoint(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    return all(a.get(k) == b.get(k) for k in _ENDPOINT)


class Realtime(IdleSupervisor[Account, MailSession]):
    IDLE_SECONDS = 24 * 60          # re-IDLE before servers drop (~29 min)
    MAX_IDLE_ACCOUNTS = 12
    _DEBOUNCE = 3                   # coalesce a burst of IDLE signals into one sync

    def __init__(self, accounts: Accounts, open_session: OpenSession):
        super().__init__()
        self._accounts = accounts
        self._open_session = open_session
        self._dirty: set[str] = set()
        self._dirty_lock = threading.Lock()
        self._kick = threading.Event()

    def _trigger_sync(self, email: str) -> None:
        with self._dirty_lock:
            self._dirty.add(email)
        self._kick.set()


    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            self._kick.wait(timeout=self._DEBOUNCE)
            if self._stop.is_set():
                break
            self._kick.clear()
            with self._dirty_lock:
                todo = self._dirty.copy()
                self._dirty.clear()
            if todo:
                bus.publish(InboxChanged(frozenset(todo)))


    # ── which accounts, and how each is reached ────────────────────────────────────────────────

    def _desired(self) -> dict[str, Account] | None:
        try:
            return {a["email"]: a for a in self._accounts.accounts()}
        except Exception as e:
            log.warning("can't read the accounts; watching as before", exc_info=e)
            return None


    def _same(self, running: Account, wanted: Account, /) -> bool:
        return _same_endpoint(running, wanted)


    def _wanted(self, acct: Account, /) -> bool:
        """Is this account still configured, enabled and reached the same way? A configuration that
        can't be read keeps the worker watching."""
        try:
            return _same_endpoint(self._accounts.get(acct["email"]), acct)
        except KeyError:
            return False
        except Exception:
            return True


    def _limit(self) -> int:
        return self.MAX_IDLE_ACCOUNTS


    def _name(self, acct: Account, /) -> str:
        return f"idle:{acct['email']}"


    def _say_unwanted(self, acct: Account, /) -> None:
        log.info("%(account)s: removed, disabled or changed; this worker stops", {"account": acct["email"]})


    def _say_failed(self, acct: Account, error: Exception, /) -> None:
        log.warning("%(account)s: IDLE failed", {"account": acct["email"]}, exc_info=error)


    # ── the IDLE loop ──────────────────────────────────────────────────────────────────────────

    def _connect(self, acct: Account, /) -> MailSession:
        return self._open_session(acct)


    def _ready(self, acct: Account, session: MailSession, /) -> bool:
        if "IDLE" not in session.capabilities:
            log.warning("%(account)s: server has no IDLE — falling back to periodic sync", {"account": acct["email"]})
            return False                   # the worker closes it as it ends, as it does any connection
        session.select("INBOX", readonly=False)
        return True


    def _serve(self, watch: Watch[Account, MailSession], session: MailSession, /) -> None:
        acct = watch.spec
        while not watch.leaving():
            changed = session.idle(self.IDLE_SECONDS)   # it leaves IDLE (sends DONE) before syncing
            if watch.leaving():
                break
            if changed:
                self._trigger_sync(acct["email"])      # sync runs on a SEPARATE short-lived conn
            if not self._wanted(acct):
                break                         # the worker asks again, says so and stops


    def _close(self, session: MailSession, /) -> None:
        try:
            session.logout()               # closes the connection once the server answers
        except Exception:
            try:
                session.shutdown()         # it didn't answer (stop() shut the socket down): close here
            except Exception:
                pass


    def start(self) -> None:
        """Launch the sync dispatcher, then one IDLE worker per enabled account (capped)."""
        self.begin()
        threading.Thread(target=self._dispatch_loop, daemon=True).start()
        configured = self.reconcile()
        if configured > self.MAX_IDLE_ACCOUNTS:
            log.warning("%d accounts > cap %d; extras rely on the periodic sync", configured, self.MAX_IDLE_ACCOUNTS)
        log.info("watching %d account(s) in real time", len(self.watching()))


    def stop(self) -> None:
        self._stop.set()                   # before the kick: the dispatcher asks as it wakes
        self._kick.set()
        super().stop()
