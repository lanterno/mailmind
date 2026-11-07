"""The folder rail, and a folder's sync as it opens.

The live IMAP LIST and each folder's STATUS take a fresh round-trip, and the rail loads on load, on every account switch
and after every triage action, so doing it live each time both lagged the page and churned connections (rapid connect
and logout makes providers drop IDLE). So that part is cached per account for a few seconds over the connection pool;
the counts from the database stay fresh."""
import re
import threading
import time
from collections.abc import Iterable
from typing import Any, TypedDict

from mailmind.kernel.errors import Refused
from mailmind.mailbox.application.server_ops import UNREACHABLE
from mailmind.mailbox.application.pool import Pool
from mailmind.mailbox.application.ports import Account, Accounts, Attention, StoredMail
from mailmind.mailbox.application.sync import Ingest
from mailmind.mailbox.domain import roles as folder_roles
from mailmind.platform import sse
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger
from mailmind.platform.workers import gates

log = get_logger("folders")
sync_log = get_logger("folder-sync")


class RailFolder(TypedDict):
    name: str
    role: str
    unseen: int
    total: int


class _Listed(TypedDict):
    folders: list[RailFolder]
    ts: float


def _rail_order(folders: Iterable[RailFolder]) -> list[RailFolder]:
    order = {"inbox": 0, "sent": 1, "drafts": 2, "archive": 3, "all": 4, "junk": 8, "trash": 9}
    return sorted(folders, key=lambda f: (order.get(f["role"], 5), f["name"].lower()))


class Folders:
    FIRST_SYNC_WAIT = 8.0      # seconds a list request waits for a folder's first scan before it answers
    CATCH_UP_WAIT = 1.5        # ... and for a catch-up of a folder read before
    CATCH_UP_EVERY = 60.0      # seconds between catch-ups of one folder as it is opened
    _FOLDER_LIST_TTL = 25.0    # folder unread/total badges may be this stale (ambient counts)

    def __init__(self, uow: UnitOfWork, store: StoredMail, accounts: Accounts, pool: Pool, ingest: Ingest, attention: Attention):
        self._uow = uow
        self._store = store
        self._accounts = accounts
        self._pool = pool
        self._ingest = ingest
        self._attention = attention
        self._folder_list_cache: dict[str, _Listed] = {}
        self._folder_list_lock = threading.Lock()
        self._folder_syncs: dict[tuple[str, str], threading.Event] = {}    # set when that folder's sync ends
        self._first_sync_failed: dict[tuple[str, str], str] = {}           # the error kind of its last first scan, while it failed
        self._caught_up: dict[tuple[str, str], float] = {}                 # time.monotonic() its last sync on open started
        self._folder_syncs_lock = threading.Lock()

    def plays_drafts(self, account: str, folder: str) -> bool:
        """Whether `folder` is this account's Drafts: what the last LIST said (its SPECIAL-USE attribute
        first, and one folder a role), else what its name says on its own. Part of a name never counts:
        "Contract drafts" holds whatever was filed there."""
        with self._folder_list_lock:
            hit = self._folder_list_cache.get(account)
        if hit:
            return any(f["name"] == folder and f["role"] == "drafts" for f in hit["folders"])
        return folder_roles.names_drafts(folder)


    def role(self, account: str, folder: str) -> str:
        """The part `folder` plays on this account: what the last LIST said, else what its name says."""
        with self._folder_list_lock:
            hit = self._folder_list_cache.get(account)
        for f in (hit["folders"] if hit else ()):
            if f["name"] == folder:
                return f["role"]
        return folder_roles.role_by_name(folder)




    def _live_folder_list(self, acct: Account) -> list[RailFolder]:
        """The expensive part: live IMAP LIST + per-folder UNSEEN/MESSAGES status,
        over a POOLED connection (reused — no per-call login/logout churn)."""
        out: list[RailFolder] = []
        with self._pool.borrow(acct) as s:
            folders = [f for f in s.folders() if f.selectable]
            playing = {folder_roles.find(folders, r): r for r in folder_roles.ROLES}   # one folder a role
            for f in folders:
                name, role = f.name, playing.get(f.name, "custom")
                try:
                    unseen, total = s.status(name)
                except Exception:
                    unseen = total = 0
                out.append({"name": name, "role": role, "unseen": unseen, "total": total})
        return _rail_order(out)


    def _stored_folder_list(self, account: str) -> list[RailFolder]:
        """The folders MailMind holds this account's mail in, from the database alone: what the rail
        shows when the server can't be reached and no live list is cached (Bridge down, its
        certificate changed). Roles by name, the shallower folder first; counts as stored."""
        with self._uow.read() as c:
            counts = self._store.stored_folder_counts(c, account)
        counts.setdefault("INBOX", (0, 0))
        names = sorted(counts, key=lambda n: (len(re.findall(r"[/.]", n)), n.lower()))
        folders = [folder_roles.Folder(n, frozenset(), None) for n in names]
        playing = {folder_roles.find(folders, r): r for r in folder_roles.ROLES}
        return _rail_order([{"name": n, "role": playing.get(n, "custom"), "unseen": counts[n][0] or 0,
                             "total": counts[n][1] or 0} for n in names])


    def _folders_list_cached(self, account: str, acct: Account) -> list[RailFolder]:
        """Folder list from the TTL cache, refreshed via live IMAP when stale. On a
        live-refresh failure, serve the last cached list (even if expired) so a
        transient IMAP hiccup doesn't 500 the rail."""
        with self._folder_list_lock:
            hit = self._folder_list_cache.get(account)
        if hit and (time.time() - hit["ts"]) < self._FOLDER_LIST_TTL:
            return hit["folders"]

        def refresh() -> list[RailFolder]:
            # double-check under the IMAP gate: a concurrent caller may have refreshed
            # while we waited, so a cold burst does ONE live fetch, not N.
            with self._folder_list_lock:
                fresh = self._folder_list_cache.get(account)
            if fresh and (time.time() - fresh["ts"]) < self._FOLDER_LIST_TTL:
                return fresh["folders"]
            folders = self._live_folder_list(acct)
            with self._folder_list_lock:
                self._folder_list_cache[account] = {"folders": folders, "ts": time.time()}
            return folders
        try:
            return gates.run(account, refresh, priority=gates.Priority.INTERACTIVE)
        except Exception as e:
            if hit:
                log.warning("%(account)s: live refresh failed, serving cached", {"account": account}, exc_info=e)
                return hit["folders"]
            if isinstance(e, UNREACHABLE):
                log.warning("%(account)s: unreachable, serving the stored folders", {"account": account}, exc_info=e)
                return self._stored_folder_list(account)
            raise


    def invalidate(self, account: str | None = None) -> None:
        """Drop the folder-list cache so the next load re-pulls live counts (called
        after a sync ingests new mail, so unread badges don't lag the TTL)."""
        with self._folder_list_lock:
            if account is None:
                self._folder_list_cache.clear()
            else:
                self._folder_list_cache.pop(account, None)


    def of_account(self, account: str) -> dict[str, Any]:
        """Folder list with roles + unread counts for one account. The live-IMAP part
        is TTL-cached + pooled; the DB-derived counts (snoozed/muted/…) are always fresh."""
        try:
            acct = self._accounts.get(account)
        except KeyError:
            raise Refused("unknown account")
        out = self._folders_list_cached(account, acct)
        with self._uow.read() as c:
            snoozed = self._store.snoozed_count(c, account)
            muted = self._attention.muted_count(c, account)
            waiting = self._attention.waiting_count(c, account)
            replylater = self._attention.replylater_count(c, account)
        return {"ok": True, "account": account, "folders": out, "snoozed": snoozed,
                "muted": muted, "waiting": waiting, "replylater": replylater}


    def _folder_sync(self, account: str, folder: str, full: bool) -> threading.Event:
        """Sync a folder as it is opened, as a job on the account's gate, unless a sync of it is
        running already: its first scan (full) or a catch-up (what came above the watermark, and the
        newest flags again), since only INBOX is synced in the background. Return the Event set when
        it ends. It ends by publishing folder-synced, with how many messages it stored, so a list that
        answered before it was done can load again."""
        key = (account, folder)
        with self._folder_syncs_lock:
            done = self._folder_syncs.get(key)
            if done is not None:
                return done
            done = self._folder_syncs[key] = threading.Event()
            self._caught_up[key] = time.monotonic()

        def run() -> None:
            ok, new = False, 0
            try:
                kept, junk, _ = gates.run(
                    account, lambda: self._ingest.sync_folder(self._accounts.get(account), folder, full=full),
                    priority=gates.Priority.INTERACTIVE if full else gates.Priority.AFTER_USER)
                ok, new = True, kept + junk
                self._first_sync_failed.pop(key, None)
            except Exception as e:
                if full:
                    self._first_sync_failed[key] = self._accounts.error_kind(e)
                sync_log.warning("%(account)s/%(folder)s", {"account": account, "folder": repr(folder)},   # a name a request gave us
                                 exc_info=e)
            finally:
                with self._folder_syncs_lock:
                    self._folder_syncs.pop(key, None)
                done.set()
            if new:
                self.invalidate(account)
            sse.publish("folder-synced", {"account": account, "folder": folder, "ok": ok, "new": new})
        threading.Thread(target=run, name="folder-sync", daemon=True).start()
        return done


    def _catch_up_due(self, account: str, folder: str) -> bool:
        """Whether opening a folder read before should catch it up: not INBOX, which the background
        sync keeps, and not again within self.CATCH_UP_EVERY seconds, unless MailMind put mail in it since.
        An open while one runs waits for it too, or it would answer with what that one is about to
        change, and nothing would say so."""
        if folder.upper() == "INBOX":
            return False
        with self._folder_syncs_lock:
            if (account, folder) in self._folder_syncs:
                return True
            last = self._caught_up.get((account, folder))
        return last is None or time.monotonic() - last >= self.CATCH_UP_EVERY


    def touched(self, account: str, folder: str) -> None:
        """MailMind put mail in `folder` (a draft saved there): its next open catches it up at once."""
        with self._folder_syncs_lock:
            self._caught_up.pop((account, folder), None)


    def list_changed(self, account: str, folder: str) -> None:
        """A copy left `folder` and its row went with it: a list of that folder on screen loads again,
        and through its catch-up shows the copy that replaced it."""
        sse.publish("reconcile", {"account": account, "folder": folder, "pruned": 1})


    def scanning_first(self, account: str, folder: str) -> bool:
        """Whether a first scan of the folder is still to finish, starting one if none runs. A list across accounts waits
        for none of them: what's stored now, then folder-synced."""
        if self._ingest.folder_synced(account, folder):
            return False
        return not self._folder_sync(account, folder, full=True).wait(0)

    def on_open(self, account: str, folder: str) -> dict[str, bool]:
        """A folder opens: its first scan, or a catch-up if one is due, waited for a moment. Returns how the
        sync went, for the list's answer."""
        if not self._ingest.folder_synced(account, folder):
            if not self._folder_sync(account, folder, full=True).wait(self.FIRST_SYNC_WAIT):
                return {"syncing": True}               # still reading: what's stored now, then folder-synced
            if (account, folder) in self._first_sync_failed:
                return {"sync_failed": True}
        elif self._catch_up_due(account, folder):
            if not self._folder_sync(account, folder, full=False).wait(self.CATCH_UP_WAIT):
                return {"catching_up": True}           # what's stored now; folder-synced says if more came
        return {}
