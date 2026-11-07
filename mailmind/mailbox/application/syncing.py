"""The background sync of each account, and reconcile: what brings the database up to what the servers hold, one job at
a time, with what each account's last try came to.

Incremental sync only ADDS new mail; it never notices messages removed on the
server (deleted/moved in webmail, other clients, server-side filters), so their
rows linger as ghosts and the message list shows phantoms. A dedicated low-rate
loop prunes them — ONE account per tick (round-robin) so each hold of the IMAP gate is a
single account, taken NON-blocking so a live user op is never stalled (mirrors the
body-backfill loop), on its own timer so the work never piggybacks a user-triggered
(IDLE) sync. reconcile_account is a SAFE pure-prune (never prunes on IMAP error,
UIDVALIDITY change, or an OK-but-empty SEARCH against a non-empty store).
"""
import json
import os
import threading
import time
from collections.abc import Callable, Collection, Mapping
from typing import Any

from mailmind.kernel.when import now_iso
from mailmind.mailbox.application.server_ops import UNREACHABLE
from mailmind.mailbox.application.folders import Folders
from mailmind.mailbox.application.ports import Account, AccountPruned, Accounts, Attention, Result, StoredMail
from mailmind.mailbox.application.stats import LiveStats
from mailmind.mailbox.application.sync import Ingest
from mailmind.mailbox.application.warm import Warming
from mailmind.mailbox.domain import sync_errors
from mailmind.platform import settings, sse
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger
from mailmind.platform.workers import gates

log = get_logger("sync")
reconcile_log = get_logger("reconcile")
warm_log = get_logger("warm")


def _reconcile_secs() -> int:
    try:
        return int(os.environ.get("MAILMIND_RECONCILE_SECS", "1800"))  # full-cycle target; 0 disables
    except ValueError:
        reconcile_log.warning("bad MAILMIND_RECONCILE_SECS — using 1800")
        return 1800


class Syncing:
    BACKFILL_RETRY = 6 * 3600     # a body the server didn't give is asked for again after this long
    BACKFILL_UNREACHABLE = 60     # the round after one whose server couldn't be reached
    _RECONCILE_INTERVAL = _reconcile_secs()

    def __init__(self, uow: UnitOfWork, store: StoredMail, accounts: Accounts, ingest: Ingest, warm: Warming, folders: Folders,
                 stats: LiveStats, attention: Attention, kick: Callable[[str], object]):
        self._uow = uow
        self._store = store
        self._accounts = accounts
        self._ingest = ingest
        self._warm = warm
        self._folders = folders
        self._stats = stats
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


    def _reconcile_folders(self, acct: Account, dry_run: bool = False, force: bool = False,
                           priority: int = gates.Priority.INTERACTIVE, wait: bool = True) -> AccountPruned:
        """Reconcile each folder a sync has stored for `acct`, each as a job of its own on the
        account's gate, so a long run never holds it across folders. Returns one result for the
        account: the counts added up, the first folder's error, and how many folders it reached. With
        wait=False it stops at the first folder whose gate is busy (gates.Busy if that's the first)."""
        out: AccountPruned = {"account": acct["email"], "live": 0, "stored": 0, "ghosts": 0, "pruned": 0,
                              "error": None, "folders": 0}
        for folder in self._ingest.reconciled_folders(acct["email"]):
            try:
                res = gates.run(acct["email"], lambda f=folder: self._ingest.reconcile_account(
                    acct, force=force, dry_run=dry_run, folder=f), priority=priority, wait=wait)
            except gates.Busy:
                if not out["folders"]:
                    raise
                break                                     # a user op came: the rest wait for next time
            out["folders"] += 1
            out["live"] += res["live"]
            out["stored"] += res["stored"]
            out["ghosts"] += res["ghosts"]
            out["pruned"] += res["pruned"]
            out["error"] = out["error"] or res["error"]
        return out


    def _auto_reconcile_one(self, acct: Account) -> None:
        """Prune one account's ghosts, folder by folder, yielding to any live user op: its IMAP gate
        is taken without waiting (skip this tick if a user op holds it — caught next cycle), then
        publish so open lists drop the pruned phantoms."""
        try:
            res = self._reconcile_folders(acct, priority=gates.Priority.MAINTENANCE, wait=False)
        except gates.Busy:
            return                                        # user op live — yield, retry next cycle
        if not res["pruned"]:
            return
        with open(settings.current().reconcile_audit, "a") as f:
            f.write(json.dumps({"kind": "reconcile", "ts": now_iso(), "auto": True,
                                "account": res["account"], "pruned": res["pruned"],
                                "live": res["live"]}) + "\n")
        self._stats.invalidate()
        self._folders.invalidate(acct["email"])                # folder badge counts changed
        try:
            sse.publish("reconcile", {"account": acct["email"], "pruned": res["pruned"]})
        except Exception:
            pass
        reconcile_log.info("auto: %(account)s pruned %(count)s ghost row(s)", {"account": acct["email"], "count": res["pruned"]})


    def reconcile_rounds(self) -> Callable[[], int]:
        """A ghost pruner's round function, with its own place in the round-robin: each round prunes
        one account and returns the seconds until the next, so the full cycle spans about
        MAILMIND_RECONCILE_SECS and each hold of the IMAP gate covers a single account."""
        rr = 0

        def reconcile_round() -> int:
            nonlocal rr
            accts = self._accounts.accounts()
            n = len(accts) or 1
            if accts:
                try:
                    self._auto_reconcile_one(accts[rr % len(accts)])
                except Exception as e:
                    reconcile_log.warning("loop error", exc_info=e)
                rr += 1
            return max(30, self._RECONCILE_INTERVAL // n)       # spread the full cycle across accounts
        return reconcile_round


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


    def do_reconcile(self, account: str | None, dry_run: bool, force: bool = False) -> dict[str, Any]:
        """Reconcile the account named, or each in turn for "all", every folder a sync has stored.
        force prunes even where the server's SEARCH came back empty against stored mail."""
        self._accounts.preload_creds()
        accts = [self._accounts.get(account)] if account and account != "all" else self._accounts.accounts()
        results = [self._reconcile_folders(a, dry_run=dry_run, force=force) for a in accts]
        if not dry_run:
            stamp = now_iso()
            with open(settings.current().reconcile_audit, "a") as f:
                for r in results:
                    if r["pruned"]:
                        f.write(json.dumps({"kind": "reconcile", "ts": stamp,
                                            "account": r["account"], "pruned": r["pruned"],
                                            "live": r["live"]}) + "\n")
            self._stats.invalidate()
        return {"ok": True, "dry_run": dry_run, "results": results,
                "total_ghosts": sum(r["ghosts"] for r in results),
                "total_pruned": sum(r["pruned"] for r in results), "generated": now_iso()}
