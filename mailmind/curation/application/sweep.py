"""Sweeping an inbox: classify it, then move its junk to Trash, verified and audited. Mail is read live before
anything moves, so a stored row can't send the wrong message anywhere."""
from collections.abc import Callable
from typing import Any

from mailmind.curation.application.ports import AuditLog, FilingCommands, LiveSession, Mailboxes, RuleBookRepository, SenderMail
from mailmind.curation.domain import sweep_plan
from mailmind.curation.domain.live import READ_FIELDS, Classified, Read, read_from_headers
from mailmind.kernel.errors import Refused
from mailmind.kernel.when import now_iso
from mailmind.platform import profile
from mailmind.platform.logging import get_logger
from mailmind.platform.workers import gates

log = get_logger("sweep")


def fetch_headers(s: LiveSession) -> list[Read]:
    """Return list of {uid, from, name, subject, bulk} for the INBOX, which `s` selects."""
    s.select("INBOX")
    return [read_from_headers(h.uid, h.raw) for h in s.headers(s.uids("ALL"), READ_FIELDS)]


class Sweeps:
    def __init__(self, rules: RuleBookRepository, mail: SenderMail, boxes: Mailboxes, audit: AuditLog,
                 filing: FilingCommands):
        self._rules, self._mail, self._boxes, self._audit, self._filing = rules, mail, boxes, audit, filing

    def plan(self, acct: dict[str, Any]) -> dict[str, Any]:
        """Connect, classify the live INBOX, and RETURN structured per-bucket stats
        (no printing). Errors are captured, not raised."""
        res: dict[str, Any] = {"account": acct["email"], "kind": acct["kind"], "in_inbox": 0,
                               "by_bucket": {}, "junk": 0, "keep": 0, "top_senders": {}, "error": None}
        try:
            with self._boxes.borrow(acct) as s:      # pooled read: reuse the login (no fresh connect per call)
                rows = sweep_plan.classified(fetch_headers(s), self._rules.sets())   # ctx-mgr ALWAYS closes → no socket leak on fetch error
        except Exception as e:
            log.warning("an account failed", exc_info=e)
            res["error"] = type(e).__name__      # what a server said stays out of the answer
            return res
        res.update(sweep_plan.tally((r["bucket"], r["from"]) for r in rows))
        res["_rows"] = rows  # internal: used by the plan command and sweep, dropped before serialization
        return res

    def plan_stored(self, acct: dict[str, Any]) -> dict[str, Any]:
        """Stored twin of plan — NO IMAP, instant. Re-classifies the STORED INBOX rows with the same
        classifier (so it reflects current keep/block rules and matches what a live sweep would move).
        Accurate because auto-reconcile keeps the stored INBOX in sync with the server. Used by the
        Curate drawer (/api/stats), which was a slow live header scan of every account; the Sweep buttons still
        re-verify live before moving anything."""
        res: dict[str, Any] = {"account": acct["email"], "kind": acct["kind"], "in_inbox": 0,
                               "by_bucket": {}, "junk": 0, "keep": 0, "top_senders": {}, "error": None}
        rows = self._mail.stored_inbox(acct["email"])
        sets = self._rules.sets()
        res.update(sweep_plan.tally(sweep_plan.stored_bucket(r, sets) for r in rows))
        return res

    def sweep(self, acct: dict[str, Any], dry: bool = False) -> dict[str, Any]:
        """Dry-run plan or verified move of an account's junk buckets to Trash.
        Prunes matching local DB rows on a real run. Returns a result dict."""
        p = self.plan(acct)
        if p["error"]:
            return {"account": acct["email"], "error": p["error"], "planned": 0}
        junk_rows: list[Classified] = sweep_plan.junk(p["_rows"])
        out: dict[str, Any] = {"account": acct["email"], "planned": len(junk_rows), "by_bucket": p["by_bucket"],
                               "senders": len(set(r["from"] for r in junk_rows)), "moved": 0,
                               "stragglers": [], "error": None}
        if dry or not junk_rows:
            return out
        self._audit.swept(acct["email"], junk_rows)  # log BEFORE moving so a crash still records intent
        try:
            s = self._boxes.open_session(acct)
            try:
                moved, stragglers = self._boxes.move_verified(s, [r["uid"] for r in junk_rows],
                                                              self._boxes.trash_folder(acct))
            finally:
                try:
                    s.logout()
                except Exception:
                    pass
        except Exception as e:
            log.warning("an account failed", exc_info=e)
            out["error"] = type(e).__name__      # what a server said stays out of the answer
            return out
        out["moved"], out["stragglers"] = moved, stragglers
        self._mail.forget_moved(acct["email"], junk_rows, stragglers)        # don't re-ghost swept mail
        return out

    def sweep_ai(self, acct: dict[str, Any], dry: bool = False) -> dict[str, Any]:
        """Trash the INBOX mail stored as is_junk=1, which is the classifier's verdict at ingest, whatever its rules say
        of the headers now (the name is older than that: no AI is asked). Matched to the LIVE inbox by Message-ID so UIDs are always
        current (no UIDVALIDITY risk), minus any keep-ruled sender/domain. Move to Trash
        (reversible), verified + audited; prune the local DB by Message-ID."""
        r = self._rules.sets()
        junk_mids = self._mail.ai_junk_ids(acct["email"])
        out: dict[str, Any] = {"account": acct["email"], "planned": 0, "moved": 0, "stragglers": [],
                               "kept_by_rule": 0, "error": None}
        if not junk_mids:
            return out
        try:
            s = self._boxes.open_session(acct)
        except Exception as e:
            log.warning("an account failed", exc_info=e)
            out["error"] = type(e).__name__      # what a server said stays out of the answer
            return out
        try:                                    # single try/finally so s is ALWAYS logged out (no socket leak)
            targets, kept = sweep_plan.ai_targets(fetch_headers(s), junk_mids, r)
            out["planned"], out["kept_by_rule"] = len(targets), kept
            if dry or not targets:
                return out
            self._audit.swept(acct["email"], sweep_plan.ai_audited(targets))
            moved, stragglers = self._boxes.move_verified(s, [t["uid"] for t in targets],
                                                          self._boxes.trash_folder(acct))
        except Exception as e:
            log.warning("an account failed", exc_info=e)
            out["error"] = type(e).__name__      # what a server said stays out of the answer
            return out
        finally:
            try:
                s.logout()
            except Exception:
                pass
        out["moved"], out["stragglers"] = moved, stragglers
        self._mail.forget_moved(acct["email"], targets, stragglers)
        return out

    def _each_account(self, account: str | None, job: Callable[[dict[str, Any]], dict[str, Any]]) -> list[dict[str, Any]]:
        """[job(acct)] for the account named, or for each account in turn when it names "all", each on
        its own account's gate alone: the accounts' work is independent, so a run over all of them
        never holds every gate at once, and one account's slow server holds up only its own turn."""
        if account and account != "all":
            try:
                acct = self._boxes.get(account)
            except KeyError:
                raise Refused("unknown account")
            return [gates.run(account, lambda: job(acct), priority=gates.Priority.INTERACTIVE)]
        return [gates.run(a["email"], lambda a=a: job(a), priority=gates.Priority.INTERACTIVE)
                for a in self._boxes.all()]

    def run(self, account: str | None, dry_run: bool) -> dict[str, Any]:
        """A sweep the person asked for, of the account named or of "all"."""
        if not dry_run:
            profile.refuse_writes("a sweep")     # it moves mail, and its moves can't wait in a queue
        self._boxes.preload_creds()
        results = self._each_account(account, lambda a: self.sweep(a, dry=dry_run))
        if not dry_run:
            self._filing.stats_changed()
        return {"ok": True, "dry_run": dry_run, "results": results,
                "generated": now_iso()}


