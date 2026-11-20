"""The background sync of each account, and the body backfill: what brings the database up to what the servers hold, one
job at a time, with what each account's last try came to.

An account shows as failing only as domain.sync_health says: trouble that may pass (a lost connection, a timeout, a
server that can't take a login for now) once a second sync in a row has failed too, or three minutes on. Until then it shows as it did before, and its record keeps no
error, but its status says it is trying again (`trying_again`), so nothing says it synced when its last sync didn't.
A sync that fails tells the open tabs (`sync-trouble`), which read the status again.

A connection to an account's server that couldn't be made outside its sync, by trouble that may pass (IDLE's, as it
connects again after its connection was lost, or a job's), says so within seconds rather than at the next round, minutes
on (`unreached`): the account shows as trying again, as after a sync that failed, the tabs are told, and the sync that
measures it runs now. It counts no failed sync: the next that fails is the run's first. One dropped connection that
connects again at once logs in, and says nothing.

While an account's trouble may pass, the background tries it again every `sync_health.RETRY_SECONDS` (`try_unreached`), so
it says it synced within moments of its server answering again, rather than at the next round. A try is its sync, run on
its own: one that fails changes nothing and says nothing, and counts no failed sync, so trouble shows as it would have,
after the round's next sync or three minutes on.

A sync that works after one of the account's failed, and a person's Retry, let go of the waits of the changes queued for its
server (`server_back`, the commands' `release`): they go now, not minutes on; and its IDLE worker, resting after a
connection that failed, connects now (Realtime's `hurry`), so new mail is heard within moments, not after a rest that may
have grown to five minutes.

An account removed (`account_removed`) stops each sync reading its mail at its next message, as its flights were told, and
nothing is kept of how its syncs went: its status, its run of failures, its body backfill's rest; now, and again as the last
sync reading its mail lands."""
import threading
import time
from collections.abc import Callable, Collection, Mapping
from typing import Any

from mailmind.kernel.errors import Refused
from mailmind.kernel.ids import INBOX
from mailmind.kernel.when import now_iso
from mailmind.mailbox.application.server_ops import UNREACHABLE
from mailmind.mailbox.application.flights import AccountGone
from mailmind.mailbox.application.folders import Folders
from mailmind.mailbox.application.ports import Accounts, Attention, Result, StoredMail
from mailmind.mailbox.application.sync import Ingest
from mailmind.mailbox.application.warm import Warming
from mailmind.mailbox.domain import body_backfill, sync_health, sync_plan
from mailmind.platform import sse
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger
from mailmind.platform.workers import gates

log = get_logger("sync")
warm_log = get_logger("warm")


class Syncing:
    def __init__(self, uow: UnitOfWork, store: StoredMail, accounts: Accounts, ingest: Ingest, warm: Warming, folders: Folders,
                 attention: Attention, kick: Callable[[str], object], server_back: Callable[[str], object] = lambda _: None):
        self._uow = uow
        self._store = store
        self._accounts = accounts
        self._ingest = ingest
        self._warm = warm
        self._folders = folders
        self._attention = attention
        self._kick = kick
        self._server_back = server_back
        self._sync: dict[str, Any] = {"running": False, "last": None, "result": "idle"}
        self._status: dict[str, dict[str, str]] = {}   # email -> {"health": ok|syncing|error, "msg": str}
        self._failing: dict[str, sync_health.Failing] = {}   # email -> its syncs failed in a row, until one works
        self._sync_lock = threading.Lock()
        self._sync_asked: list[frozenset[str] | None] = []   # syncs asked for while one ran: each its accounts, or None for all
        self._backfill_missed: dict[int, body_backfill.SetAside] = {}   # pk -> when backfill may ask for its body again
        self._backfill_resting: dict[str, float] = {}        # email -> when backfill may ask anything of that account again

    @property
    def status(self) -> dict[str, dict[str, Any]]:
        """How each account's sync stands now: one whose failures in a row now show, three minutes on, as failing; one
        whose don't show yet as it did before, trying again. One syncing now says that alone."""
        now = time.time()
        shown: dict[str, dict[str, Any]] = dict(self._status)
        for email, run in list(self._failing.items()):
            if shown.get(email, {}).get("health") == "syncing":
                continue
            if run.shows(now):
                shown[email] = {"health": "error", "msg": run.error}
            else:   # as it was, but its last sync failed: nothing may say it synced. One no sync has reached has no health
                shown[email] = {**shown.get(email, {"health": "", "msg": ""}), "trying_again": True}
        return shown

    def account_removed(self, email: str) -> None:
        """AccountRemoved: each sync reading its mail stops at its next message, and nothing is kept of how its syncs
        went, now or as the last of them lands, so a sync that ended meanwhile leaves nothing either."""
        self._ingest.flights.stop(email)
        self._forget(email)
        self._ingest.flights.after(email, lambda: self._forget(email))

    def _forget(self, email: str) -> None:
        """Nothing is kept of how a removed account's syncs went: its status, its run of failures, its backfill's rest."""
        self._status.pop(email, None)
        self._failing.pop(email, None)
        self._backfill_resting.pop(email, None)

    @property
    def sync(self) -> dict[str, Any]:
        """How the sync as a whole stands now."""
        return self._sync

    def sync_accounts(self, emails: Collection[str] | None = None, tries: Collection[str] = ()) -> int:
        """Sync the given account emails (or all when None). Emits realtime events. Caller
        must hold the _sync single-flight guard (sync_once / sync_guarded).
        An Inbox's first scan stores mail that was there before, so it is no new mail: it counts
        none and says no new-mail. Of `tries`, a sync that fails by trouble that may pass changes nothing (`try_unreached`)."""
        new = 0
        for a in self._accounts.accounts():
            if emails is not None and a["email"] not in emails:
                continue
            # While it reads, a removal stops it at its next message; then nothing more is said or kept of the account,
            # and a sync that finds it gone already has nothing to say either.
            with self._ingest.flights.fly(a["email"]) as flight:
                before = self._status.get(a["email"])
                self._status[a["email"]] = {"health": "syncing", "msg": ""}
                try:
                    with self._uow.read() as c:
                        first = self._store.get_watermark(c, a["email"], INBOX) == 0
                    first = first and not self._ingest.folder_synced(a["email"], INBOX)
                    kept, junk, newest = self._ingest.sync_new(a)
                    flight.going_on()
                    arrived = 0 if first else kept
                    new += arrived
                    unreached = self._failing.pop(a["email"], None) is not None
                    self._status[a["email"]] = {"health": "ok", "msg": f"+{kept}"}
                    with self._uow.write() as c:
                        self._store.record_sync_result(c, a["email"], True)
                    if unreached:      # the server answers again: what was queued for it while it didn't goes now
                        self._let_go(a["email"])
                    sse.publish("sync-done", {"account": a["email"], "kept": kept, "junk": junk})
                    if arrived > 0:     # one message names its UID, so a notification can say whose it is and open it
                        one = {"uid": newest} if arrived == 1 else {}
                        sse.publish("new-mail", {"account": a["email"], "folder": "INBOX", "count": arrived, **one})
                    elif kept or junk:
                        self._folders.invalidate(a["email"])   # what the first scan stored changes the rail's counts
                except AccountGone:
                    self._forget(a["email"])
                except Exception as e:
                    if flight.stopped:
                        self._forget(a["email"])
                        continue
                    if a["email"] in tries and self._accounts.may_pass(e):
                        # A try of a server that still doesn't answer: the account shows as it did, its run as it was.
                        self._show_as_before(a["email"], before)
                        continue
                    kind, now = self._accounts.error_kind(e), time.time()
                    run = sync_health.and_one_more(self._failing.get(a["email"]), type(e).__name__,
                                                   self._accounts.may_pass(e), now)
                    self._failing[a["email"]] = run
                    if not run.shows(now):     # it may pass: the account shows as it did, and the next sync says
                        self._show_as_before(a["email"], before)
                        log.info("an account's sync failed; it shows if the next fails too", exc_info=e)
                        sse.publish("sync-trouble", {"account": a["email"]})
                        continue
                    self._status[a["email"]] = {"health": "error", "msg": f"{type(e).__name__}"}
                    sse.publish("sync-trouble", {"account": a["email"]})
                    with self._uow.write() as c:
                        self._store.record_sync_result(c, a["email"], False, type(e).__name__, kind)
                    log.warning("%(account)s: sync failed", {"account": a["email"]}, exc_info=e)
        return new


    def _show_as_before(self, email: str, before: dict[str, str] | None) -> None:
        """The account shows as it did before a sync whose failure may pass."""
        if before is None:
            self._status.pop(email, None)
        else:
            self._status[email] = before


    def try_unreached(self) -> None:
        """Try again each account whose syncs fail by trouble that may pass: its sync, on its own. One that works ends the
        trouble as any sync that works does; one that fails changes nothing (`sync_accounts`). Nothing else a round does goes
        with it: no snooze wakes, no folder is warmed, and the round's last end stays as it was. A sync running now says how
        each went, so a try waits for none; one asked for while the try ran goes after it."""
        trying = frozenset(email for email, run in list(self._failing.items()) if run.passing)
        if not trying:
            return
        with self._sync_lock:
            if self._sync["running"]:
                return
            self._sync["running"] = True
        try:
            new = self.sync_accounts(trying, tries=trying)
        finally:
            with self._sync_lock:
                asked = self._sync_asked[:]
                self._sync_asked.clear()
                self._sync["running"] = False
        if new:
            self._folders.invalidate()
            self._kick("assess")
        if asked:
            self.sync_guarded(sync_plan.asked_again(asked))


    def unreached(self, email: str, e: BaseException) -> None:
        """A connection to `email`'s server, made outside its sync, couldn't be made (IDLE's as it connects again, or a
        job's): trouble that may pass shows at once as a sync that failed does, trying again, rather than at the next sync,
        minutes on; the tabs hear of it, and the sync that measures it runs now. The run counts no failed sync, so the next
        that fails is its first. Nothing changes while the account's sync runs, which says how it went, or once its syncs
        fail already, or for an account no longer kept; any other trouble is the sync's to say."""
        if not self._accounts.may_pass(e):
            return
        try:
            kept = any(a["email"] == email for a in self._accounts.accounts())
        except Exception:
            return
        if not kept or self._status.get(email, {}).get("health") == "syncing" or email in self._failing:
            return
        # Its next sync that works lets the changes queued for its server go, as after a sync that failed.
        self._failing[email] = sync_health.heard(type(e).__name__, time.time())
        log.info("a connection to an account's server failed; it is trying again", exc_info=e)
        sse.publish("sync-trouble", {"account": email})
        self._kick("sync")


    def sync_record(self) -> list[dict[str, Any]]:
        """What each account's syncs have come to (last ok/error + coarse kind), and how its sync
        stands now, for Accounts to read as its health. Pure read; no IMAP, no IMAP gate."""
        with self._uow.read() as c:
            rows = self._store.account_health(c)
        live = self.status
        return [{"account": r["account"], "last_ok_ts": r["last_ok_ts"], "last_error_ts": r["last_error_ts"],
                 "last_error_kind": r["last_error_kind"], "last_error": r["last_error"], "sync_count": r["sync_count"],
                 "live": live.get(r["account"], {}).get("health", "")}
                for r in rows if (r["folder"] or "INBOX") == "INBOX"]


    def do_sync_retry(self, body: Mapping[str, Any]) -> Result:
        """Clear an account's recorded sync error and re-run its sync via the existing single-flight."""
        acct = (body.get("account") or "").strip()
        if acct not in {a["email"] for a in self._accounts.accounts()}:
            raise Refused("unknown account")
        with self._uow.write() as c:
            self._store.clear_sync_error(c, acct)
        self._accounts.try_login_again(acct)
        self._let_go(acct)     # a person asked: what waits for the account's server goes with its sync, whatever the last came to
        threading.Thread(target=lambda: self.sync_guarded({acct}), daemon=True).start()
        return {"ok": True, "account": acct}


    def _let_go(self, email: str) -> None:
        """The changes waiting for the account's server go now. One that can't be let go waits out its backoff, as before."""
        try:
            self._server_back(email)
        except Exception as e:
            log.warning("the changes waiting for a server weren't let go", exc_info=e)


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
                emails = sync_plan.asked_again(asked)
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
            self._warm.warm_tick(emails)        # optional background folder warming (OFF unless asked for: warm.py)
        except Exception as e:
            warm_log.warning("tick error", exc_info=e)


    def backfill_round(self) -> int:
        """Fetch the bodies of up to a dozen messages that have none, each account in turn, a second and a half apart,
        yielding at once to a live user op. Returns the seconds until the next round. What each failure costs, and whom,
        is domain.body_backfill's to say: none of it holds another message or another account back."""
        now = time.time()
        for account in [a for a, until in self._backfill_resting.items() if until <= now]:
            del self._backfill_resting[account]
        emails = [a["email"] for a in self._accounts.accounts() if a["email"] not in self._backfill_resting]
        with self._uow.read() as c:
            left = set(self._store.still_without_body(c, list(self._backfill_missed)))
            for pk in set(self._backfill_missed) - left:   # opened meanwhile, or gone
                del self._backfill_missed[pk]
            skip = body_backfill.still_waiting(self._backfill_missed, now)
            waiting = {e: self._store.bodies_to_fetch(c, e, skip, body_backfill.ROUND) for e in emails}
        tried: list[int] = []
        over: set[str] = set()               # the accounts whose turn this round has ended
        for r in body_backfill.in_turn(list(waiting.values())):
            pk, account = r["pk"], r["account"]
            if account in over:
                continue
            try:
                gates.run(account, lambda: self._ingest.fetch_body(pk), priority=gates.Priority.MAINTENANCE, wait=False)
            except gates.Busy:
                over.add(account)            # a user op is live — yield and retry next cycle
                continue
            except UNREACHABLE:
                lost_at = time.time()
                self._backfill_missed[pk] = body_backfill.connection_lost(self._backfill_missed.get(pk), lost_at)
                self._backfill_resting[account] = lost_at + body_backfill.UNREACHABLE_REST
                over.add(account)
                continue
            except Exception:
                pass
            tried.append(pk)
            time.sleep(1.5)                  # gentle pacing between IMAP fetches
        if tried:
            with self._uow.read() as c:
                missed = set(self._store.still_without_body(c, tried))
            for pk in tried:
                if pk in missed:
                    self._backfill_missed[pk] = body_backfill.not_given(now)
                else:
                    self._backfill_missed.pop(pk, None)
        return body_backfill.next_round(going=any(rows and e not in self._backfill_resting for e, rows in waiting.items()),
                                        resting=bool(self._backfill_resting))
