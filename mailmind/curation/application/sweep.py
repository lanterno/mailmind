"""Sweeping an inbox: classify it, then move its junk to Trash, verified and audited. Mail is read live before
anything moves, so a stored row can't send the wrong message anywhere."""
import email
from collections import Counter, defaultdict
from collections.abc import Callable
from typing import Any

from mailmind.content import mime
from mailmind.content.mime import dec
from mailmind.curation.application.ports import AuditLog, FilingCommands, LiveSession, Mailboxes, RuleBookRepository, SenderMail
from mailmind.curation.domain import classifier
from mailmind.curation.domain.live import Classified, Read
from mailmind.curation.domain.rulebook import domain_listed
from mailmind.kernel.when import now_iso
from mailmind.platform import profile
from mailmind.platform.logging import get_logger
from mailmind.platform.workers import gates

log = get_logger("sweep")

HDR_FIELDS = ("FROM", "SUBJECT", "MESSAGE-ID", "LIST-UNSUBSCRIBE", "LIST-ID")


def fetch_headers(s: LiveSession) -> list[Read]:
    """Return list of {uid, from, name, subject, bulk} for the INBOX, which `s` selects."""
    s.select("INBOX")
    rows: list[Read] = []
    for h in s.headers(s.uids("ALL"), HDR_FIELDS):
        msg = email.message_from_bytes(h.raw)
        name, addr = mime.sender_of(msg)
        rows.append({"uid": str(h.uid), "from": addr.lower(), "name": dec(name),
                     "subject": dec(msg.get("Subject", "")),
                     "mid": (msg.get("Message-ID", "") or "").strip(),
                     "bulk": bool(msg.get("List-Unsubscribe") or msg.get("List-ID"))})
    return rows


class Sweeps:
    def __init__(self, rules: RuleBookRepository, mail: SenderMail, boxes: Mailboxes, audit: AuditLog,
                 filing: FilingCommands):
        self._rules, self._mail, self._boxes, self._audit, self._filing = rules, mail, boxes, audit, filing

    def classify_rows(self, rows: list[Read]) -> list[Classified]:
        sets = self._rules.sets()
        out: list[Classified] = []
        for r in rows:
            bucket, conf, reason = classifier.classify(r["from"], r["subject"], r["name"], r["bulk"], rules=sets)
            out.append({**r, "bucket": bucket, "conf": conf, "reason": reason})
        return out

    def plan(self, acct: dict[str, Any]) -> dict[str, Any]:
        """Connect, classify the live INBOX, and RETURN structured per-bucket stats
        (no printing). Errors are captured, not raised."""
        res: dict[str, Any] = {"account": acct["email"], "kind": acct["kind"], "in_inbox": 0,
                               "by_bucket": {}, "junk": 0, "keep": 0, "top_senders": {}, "error": None}
        try:
            with self._boxes.borrow(acct) as s:      # pooled read: reuse the login (no fresh connect per call)
                rows = self.classify_rows(fetch_headers(s))   # ctx-mgr ALWAYS closes → no socket leak on fetch error
        except Exception as e:
            log.warning("an account failed", exc_info=e)
            res["error"] = type(e).__name__      # what a server said stays out of the answer
            return res
        by: defaultdict[str, list[Classified]] = defaultdict(list)
        for r in rows:
            by[r["bucket"]].append(r)
        res["in_inbox"] = len(rows)
        res["by_bucket"] = {b: len(rs) for b, rs in by.items()}
        res["junk"] = sum(len(by[b]) for b in classifier.JUNK_BUCKETS)
        res["keep"] = len(rows) - res["junk"]
        res["top_senders"] = {b: [s for s, _ in Counter(r["from"] for r in rs).most_common(4)]
                              for b, rs in by.items()}
        res["_rows"] = rows  # internal: used by the plan command and sweep, dropped before serialization
        return res

    def plan_stored(self, acct: dict[str, Any]) -> dict[str, Any]:
        """Stored twin of plan — NO IMAP, instant. Re-classifies the STORED INBOX rows with the same
        classifier (so it reflects current keep/block rules and matches what a live sweep would move).
        Accurate because auto-reconcile keeps the stored INBOX in sync with the server. Used by the
        Curate drawer (/api/stats), which was a ~3s live 8-account header scan; the Sweep buttons still
        re-verify live before moving anything."""
        res: dict[str, Any] = {"account": acct["email"], "kind": acct["kind"], "in_inbox": 0,
                               "by_bucket": {}, "junk": 0, "keep": 0, "top_senders": {}, "error": None}
        rows = self._mail.stored_inbox(acct["email"])
        sets = self._rules.sets()
        by: defaultdict[str, list[str]] = defaultdict(list)
        for r in rows:
            addr = (r["from_addr"] or "").lower()
            # The stored row keeps no List header, and a sweep reads them live. What ingest saw in them
            # shows in the bucket: the classifier's rules come first, so a rule made since still wins.
            listed = r["bucket"] == classifier.MKT or bool(r["list_unsub_raw"])
            by[classifier.classify(addr, r["subject"] or "", r["from_name"] or "", listed, rules=sets)[0]].append(addr)
        res["in_inbox"] = len(rows)
        res["by_bucket"] = {b: len(v) for b, v in by.items()}
        res["junk"] = sum(len(by[b]) for b in classifier.JUNK_BUCKETS)
        res["keep"] = len(rows) - res["junk"]
        res["top_senders"] = {b: [s for s, _ in Counter(v).most_common(4)] for b, v in by.items()}
        return res

    def sweep(self, acct: dict[str, Any], dry: bool = False) -> dict[str, Any]:
        """Dry-run plan or verified move of an account's junk buckets to Trash.
        Prunes matching local DB rows on a real run. Returns a result dict."""
        p = self.plan(acct)
        if p["error"]:
            return {"account": acct["email"], "error": p["error"], "planned": 0}
        junk_rows: list[Classified] = [r for r in p["_rows"] if r["bucket"] in classifier.JUNK_BUCKETS]
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
        """AI-verdict sweep: trash INBOX messages MailMind flagged is_junk=1 (the AI
        classifier that reads the body — catches header-less newsletters the classifier's
        header sweep misses). Matched to the LIVE inbox by Message-ID so UIDs are always
        current (no UIDVALIDITY risk), minus any keep-ruled sender/domain. Move to Trash
        (reversible), verified + audited; prune the local DB by Message-ID."""
        r = self._rules.sets()
        keep_senders, keep_domains = r["keep_senders"], r["keep_domains"]

        def _keep_ruled(fa: str | None) -> bool:
            fa = (fa or "").lower(); dom = fa.partition("@")[2]
            return fa in keep_senders or domain_listed(dom, keep_domains)

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
            live = fetch_headers(s)
            targets: list[Read] = []
            kept = 0
            for h in live:
                if h.get("mid") and h["mid"] in junk_mids:
                    if _keep_ruled(h["from"]):
                        kept += 1
                    else:
                        targets.append(h)
            out["planned"], out["kept_by_rule"] = len(targets), kept
            if dry or not targets:
                return out
            self._audit.swept(acct["email"], [{"uid": t["uid"], "from": t["from"], "subject": t["subject"],
                                               "bucket": "ai-junk", "reason": "is_junk (AI verdict)"} for t in targets])
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
            return [gates.run(account, lambda: job(self._boxes.get(account)), priority=gates.Priority.INTERACTIVE)]
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
                "total_planned": sum(r.get("planned", 0) for r in results),
                "total_moved": sum(r.get("moved", 0) for r in results), "generated": now_iso()}


