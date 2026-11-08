"""What a sync can't notice: mail gone from the server, deleted or moved in webmail, another client or a server-side
filter. Incremental sync only ADDS new mail, so those rows linger as ghosts and the message list shows phantoms. A dedicated
low-rate loop prunes them: ONE account per tick (round-robin), so each hold of the IMAP gate is a single account, taken
NON-blocking so a live user op is never stalled (mirrors the body-backfill loop), on its own timer so the work never
piggybacks a user-triggered (IDLE) sync. reconcile_account is a SAFE pure-prune (never prunes on IMAP error, UIDVALIDITY
change, or an OK-but-empty SEARCH against a non-empty store).
"""
import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from mailmind.kernel.errors import Refused
from mailmind.kernel.ids import INBOX, AccountId, FolderName
from mailmind.kernel.when import now_iso
from mailmind.mailbox.application.folders import Folders
from mailmind.mailbox.application.ports import Account, AccountPruned, Accounts, OpenSession, Pruned, StoredMail
from mailmind.mailbox.application.stats import LiveStats
from mailmind.platform import environment, settings, sse
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger
from mailmind.platform.workers import gates

reconcile_log = get_logger("reconcile")


class Reconciler:
    _RECONCILE_INTERVAL = environment.RECONCILE_SECS.value()    # full-cycle target; 0 disables

    def __init__(self, uow: UnitOfWork, store: StoredMail, accounts: Accounts, open_session: OpenSession, folders: Folders,
                 stats: LiveStats):
        self._uow = uow
        self._store = store
        self._accounts = accounts
        self._open_session = open_session
        self._folders = folders
        self._stats = stats

    def reconciled_folders(self, email: AccountId) -> list[FolderName]:
        """The folders reconcile looks at for an account: each one a sync has stored (it has a
        sync_state row), INBOX first."""
        with self._uow.read() as c:
            folders = self._store.synced_folders(c, email)
        return folders or [INBOX]


    def reconcile_account(self, acct: Account, force: bool = False, dry_run: bool = False, folder: FolderName = INBOX) -> Pruned:
        """Prune `folder`'s rows whose UID is no longer live, and stamp survivors verified.
        Pure prune — preserves summaries of survivors and never lowers the watermark.
        SAFETY: never deletes on an IMAP error, and treats an OK-but-empty SEARCH
        against a non-empty stored set as 'unknown' (prunes nothing) unless force.
        Only rows stored before the SEARCH may go: the 300 s sync passes no IMAP gate, so
        it can store mail while this runs, and that mail is missing from the SEARCH's answer.
        dry_run computes the ghost count without changing anything."""
        res: Pruned = {"account": acct["email"], "live": 0, "stored": 0, "ghosts": 0,
                       "pruned": 0, "error": None}
        with self._uow.read() as c:
            stored_uids = self._store.stored_uids(c, acct["email"], folder)
        res["stored"] = len(stored_uids)
        try:
            s = self._open_session(acct, timeout=15)   # background/maintenance path: a hung account must
            try:                                          # not pin the IMAP gate for the interactive 60s
                uidv = s.select(folder).uidvalidity
                live = set(s.uids("ALL"))                 # a refused SEARCH raises: nothing is pruned
            finally:
                try:
                    s.logout()                            # ALWAYS close the socket (no leak on select/SEARCH error)
                except Exception:
                    pass
        except Exception as e:
            reconcile_log.warning("an account failed", exc_info=e)
            res["error"] = type(e).__name__      # what a server said stays out of the answer
            return res  # never prune on error
        # UIDVALIDITY guard: if the server renumbered UIDs (mailbox recreation / Bridge
        # re-index), `live` is in a DIFFERENT uid space than the stored uids — comparing
        # them would prune the ENTIRE real INBOX as "ghosts". Never prune here; sync_folder
        # detects the change and does the reset + full re-sync on its own pass.
        with self._uow.read() as c:
            stored_uidv = self._store.get_uidvalidity(c, acct["email"], folder)
        if uidv and stored_uidv and uidv != stored_uidv:
            res["error"] = f"UIDVALIDITY changed ({stored_uidv}->{uidv}) — prune skipped"
            return res
        res["live"] = len(live)
        res["ghosts"] = sum(1 for u in stored_uids if u not in live)
        if not live and res["stored"] > 0 and not force:
            res["error"] = "empty SEARCH against non-empty store — skipped (use force)"
            return res
        if dry_run:
            return res
        when = datetime.now(timezone.utc).isoformat()
        with self._uow.write() as c:
            pruned, _ = self._store.reconcile(c, acct["email"], folder, live, when, among=set(stored_uids))
        res["pruned"] = pruned
        return res


    def _reconcile_folders(self, acct: Account, dry_run: bool = False, force: bool = False,
                           priority: int = gates.Priority.INTERACTIVE, wait: bool = True) -> AccountPruned:
        """Reconcile each folder a sync has stored for `acct`, each as a job of its own on the
        account's gate, so a long run never holds it across folders. Returns one result for the
        account: the counts added up, the first folder's error, and how many folders it reached. With
        wait=False it stops at the first folder whose gate is busy (gates.Busy if that's the first)."""
        out: AccountPruned = {"account": acct["email"], "live": 0, "stored": 0, "ghosts": 0, "pruned": 0,
                              "error": None, "folders": 0}
        for folder in self.reconciled_folders(acct["email"]):
            try:
                res = gates.run(acct["email"], lambda f=folder: self.reconcile_account(
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


    def do_reconcile(self, account: str | None, dry_run: bool, force: bool = False) -> dict[str, Any]:
        """Reconcile the account named, or each in turn for "all", every folder a sync has stored.
        force prunes even where the server's SEARCH came back empty against stored mail."""
        self._accounts.preload_creds()
        try:
            accts = [self._accounts.get(account)] if account and account != "all" else self._accounts.accounts()
        except KeyError:
            raise Refused("unknown account")
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
