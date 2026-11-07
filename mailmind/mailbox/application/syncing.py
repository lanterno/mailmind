"""The background sync of each account, and the body backfill: what brings the database up to what the servers hold, one
job at a time, with what each account's last try came to."""
import threading
import time
from collections.abc import Callable, Collection, Mapping
from typing import Any

from mailmind.kernel.when import now_iso
from mailmind.mailbox.application.server_ops import UNREACHABLE
from mailmind.mailbox.application.folders import Folders
from mailmind.mailbox.application.ports import Accounts, Attention, Result, StoredMail
from mailmind.mailbox.application.sync import Ingest
from mailmind.mailbox.application.warm import Warming
from mailmind.mailbox.domain import sync_errors
from mailmind.platform import sse
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger
from mailmind.platform.workers import gates

log = get_logger("sync")
warm_log = get_logger("warm")


class Syncing:
    BACKFILL_RETRY = 6 * 3600     # a body the server didn't give is asked for again after this long
    BACKFILL_UNREACHABLE = 60     # the round after one whose server couldn't be reached

    def __init__(self, uow: UnitOfWork, store: StoredMail, accounts: Accounts, ingest: Ingest, warm: Warming, folders: Folders,
                 attention: Attention, kick: Callable[[str], object]):
        self._uow = uow
        self._store = store
        self._accounts = accounts
        self._ingest = ingest
        self._warm = warm
        self._folders = folders
        self._attention = attention
        self._kick = kick
        self._sync: dict[str, Any] = {"running": False, "last": None, "result": "idle"}
        self._status: dict[str, dict[str, str]] = {}   # email -> {"health": ok|syncing|error, "msg": str}
        self._sync_lock = threading.Lock()
        self._sync_asked: list[frozenset[str] | None] = []   # syncs asked for while one ran: each its accounts, or None for all
        self._backfill_missed: dict[int, float] = {}         # pk -> when backfill may ask for its body again

    @property
    def status(self) -> dict[str, dict[str, str]]:
        """How each account's sync stands now."""
        return self._status

    @property
    def sync(self) -> dict[str, Any]:
        """How the sync as a whole stands now."""
        return self._sync

    def sync_accounts(self, emails: Collection[str] | None = None) -> int:
        """Sync the given account emails (or all when None). Emits realtime events. Caller
        must hold the _sync single-flight guard (sync_once / sync_guarded)."""
        new = 0
        for a in self._accounts.accounts():
            if emails is not None and a["email"] not in emails:
                continue
            self._status[a["email"]] = {"health": "syncing", "msg": ""}
            try:
                kept, junk, _ = self._ingest.sync_new(a)
                new += kept
                self._status[a["email"]] = {"health": "ok", "msg": f"+{kept}"}
                with self._uow.write() as c:
                    self._store.record_sync_result(c, a["email"], True)
                sse.publish("sync-done", {"account": a["email"], "kept": kept, "junk": junk})
                if kept > 0:
                    sse.publish("new-mail", {"account": a["email"], "folder": "INBOX", "count": kept})
            except Exception as e:
                self._status[a["email"]] = {"health": "error", "msg": f"{type(e).__name__}"}
                with self._uow.write() as c:
                    self._store.record_sync_result(c, a["email"], False, type(e).__name__, sync_errors.kind_of(e))
                log.warning("%(account)s: sync failed", {"account": a["email"]}, exc_info=e)
        return new


    def account_health_json(self) -> dict[str, Any]:
        """Per-account durable sync health (last ok/error + coarse kind), merged with the in-flight
        _status. Pure read; no IMAP, no IMAP gate."""
        with self._uow.read() as c:
            rows = self._store.account_health(c)
        out: list[dict[str, Any]] = []
        for r in rows:
            if (r["folder"] or "INBOX") != "INBOX":
                continue
            live = self._status.get(r["account"], {})
            healthy = bool(r["last_ok_ts"]) and not r["last_error"]
            out.append({"account": r["account"], "last_ok_ts": r["last_ok_ts"],
                        "last_error_ts": r["last_error_ts"], "last_error_kind": r["last_error_kind"],
                        "last_error": r["last_error"], "sync_count": r["sync_count"],
                        "healthy": healthy, "live": live.get("health", "")})
        return {"ok": True, "accounts": out, "generated": now_iso()}


    def do_sync_retry(self, body: Mapping[str, Any]) -> Result:
        """Clear an account's recorded sync error and re-run its sync via the existing single-flight."""
        acct = (body.get("account") or "").strip()
        if acct not in {a["email"] for a in self._accounts.accounts()}:
            return {"ok": False, "error": "unknown account"}, 400
        with self._uow.write() as c:
            self._store.clear_sync_error(c, acct)
        threading.Thread(target=lambda: self.sync_guarded({acct}), daemon=True).start()
        return {"ok": True, "account": acct}, 200


    def sync_once(self) -> None:
        self.sync_guarded(None)


    def sync_rounds(self) -> Callable[[], None]:
        """The background sync's round function. Its first round resolves the credentials still
        waiting (1Password's) before it syncs, so a long-running server never has to ask again."""
        warmed = False

        def sync_round() -> None:
            nonlocal warmed
            if not warmed:
                warmed = True
                n = self._accounts.preload_creds()  # 0 when every credential is plaintext
                if n:
                    log.info("warmed %d 1Password-backed credential(s)", n)
            self.sync_once()
        return sync_round


    def sync_guarded(self, emails: Collection[str] | None) -> None:
        """Single-flight sync of `emails` (or all). Both the 300s loop and the IDLE dispatcher route
        through here so syncs never overlap. A sync asked for while one runs isn't dropped: the
        running one goes round again as it ends, once for every such ask, so mail IDLE heard about
        in the meantime is fetched now rather than at the next scheduled sync."""
        with self._sync_lock:
            if self._sync["running"]:
                self._sync_asked.append(None if emails is None else frozenset(emails))
                return
            self._sync["running"] = True
        try:
            while True:
                self._sync_round(emails)
                with self._sync_lock:
                    if not self._sync_asked:
                        self._sync["running"] = False
                        return
                    asked = self._sync_asked[:]
                    self._sync_asked.clear()
                emails = None if None in asked else {e for one in asked if one is not None for e in one}
        except BaseException:
            with self._sync_lock:
                self._sync["running"] = False
            raise


    def _sync_round(self, emails: Collection[str] | None) -> None:
        """One sync of `emails` (or all). Wakes due snoozes first. New mail kicks the assess job,
        which triages it outside this single-flight."""
        self._attention.wake_due()
        new = self.sync_accounts(emails)
        if new:
            self._folders.invalidate()         # new mail → let the rail re-pull unread badges now, not after the TTL
            self._kick("assess")
        self._sync["result"] = f"{new} new"
        self._sync["last"] = now_iso()
        log.info("%d new", new)
        try:
            self._warm.warm_tick(emails)        # optional background folder warming (OFF unless MAILMIND_WARM_FOLDERS)
        except Exception as e:
            warm_log.warning("tick error", exc_info=e)


    def backfill_round(self) -> int:
        """Fetch the bodies of up to 12 messages that have none, newest first, a second and a half
        apart, yielding at once to a live user op. Returns the seconds until the next round: 8 while
        there is a backlog, ten minutes once caught up (new mail lands body-less).

        A body the server didn't give (deleted elsewhere and not yet pruned here, or refused) waits
        self.BACKFILL_RETRY before it is asked for again, so the next round reaches older mail. A server
        that can't be reached ends the round, and the next comes in self.BACKFILL_UNREACHABLE."""
        now = time.time()
        for pk in [pk for pk, at in self._backfill_missed.items() if at <= now]:
            del self._backfill_missed[pk]
        with self._uow.read() as c:
            rows = self._store.bodies_to_fetch(c, self._backfill_missed)
        if not rows:
            return 600
        tried: list[int] = []
        wait = 8                             # drain a backlog briskly
        for r in rows:
            try:
                gates.run(r["account"], lambda: self._ingest.fetch_body(r["pk"]),
                          priority=gates.Priority.MAINTENANCE, wait=False)
            except gates.Busy:
                break                        # a user op is live — yield and retry next cycle
            except UNREACHABLE:
                wait = self.BACKFILL_UNREACHABLE  # the server's down: ask nothing more of it this round
                break
            except Exception:
                pass
            tried.append(r["pk"])
            time.sleep(1.5)                  # gentle pacing between IMAP fetches
        if tried:
            with self._uow.read() as c:
                missed = self._store.still_without_body(c, tried)
            for pk in missed:
                self._backfill_missed[pk] = now + self.BACKFILL_RETRY
        return wait
