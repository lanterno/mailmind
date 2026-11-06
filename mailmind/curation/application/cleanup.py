"""Cleaning up after one sender: who is worth it, what it would do, and doing it. Block and sweep are reversible
and stand whatever the unsubscribe does; the unsubscribe can't be taken back, so it wants the preview's token."""
import secrets
import time
import urllib.parse

from mailmind.curation.application.ports import AuditLog, FilingCommands, RuleBookRepository, SenderMail
from mailmind.curation.application.unsubscribe import Unsubscribing
from mailmind.curation.domain import noise, rulebook, unsubscribe
from mailmind.kernel.when import now_iso, rel_time

BUCKETS = ("newsletters/marketing", "automated-notifications")
MIN_COUNT = 3
LIMIT = 40
SWEEP_CAP = 200          # bound the per-sender IMAP-move burst; report if capped


class Cleanup:
    def __init__(self, rules: RuleBookRepository, mail: SenderMail, unsubscribing: Unsubscribing,
                 filing: FilingCommands, audit: AuditLog, secret):
        self._rules, self._mail, self._unsub = rules, mail, unsubscribing
        self._filing, self._audit, self._secret = filing, audit, secret

    def reverse_block(self, d):
        """Undo of a block: the rule goes, and what it decided is classified again."""
        try:
            self._rules.remove(d["token"])
            self._filing.reclassify(d["token"])
        except Exception:
            return False, d.get("account"), None
        return True, d.get("account"), None

    def senders(self, qs):
        """Ranked cleanup-candidate senders — pure local SQL, no AI, no IMAP, no outbound. Grouped by
        lower(from_addr) across all accounts+folders, bucket-filtered, post-filtered against rules so an
        already-decided sender never reappears."""
        buckets = [b for b in ((qs.get("buckets") or [",".join(BUCKETS)])[0]).split(",") if b]
        try:
            min_count = max(1, int((qs.get("min_count") or [MIN_COUNT])[0]))
        except Exception:
            min_count = MIN_COUNT
        try:
            limit = max(1, min(200, int((qs.get("limit") or [LIMIT])[0])))
        except Exception:
            limit = LIMIT
        r = self._rules.sets()
        now = time.time()
        cands = []
        for row in self._mail.noisy_senders(buckets, min_count):
            addr = row["addr"]
            dom = addr.rpartition("@")[2]
            if rulebook.decided(addr, dom, r):   # a rule covers sub-domains
                continue
            n = row["n"] or 0
            unseen = row["unseen"] or 0
            ratio = (unseen / n) if n else 0
            age_days = max(0.0, (now - (row["last_ts"] or now)) / 86400.0)
            score = noise.noise_score(n, unseen, age_days)
            if score < noise.MIN_SCORE:
                continue
            cands.append({"from_addr": addr, "from_name": row["name"] or addr, "domain": dom,
                          "count": n, "unseen": unseen, "unseen_ratio": round(ratio, 2),
                          "last_seen": rel_time(row["last_iso"]), "bucket": row["bucket"],
                          "lu_likely": row["bucket"] == "newsletters/marketing",
                          "noise_score": round(score, 2)})
        cands.sort(key=lambda x: -x["noise_score"])
        cands = cands[:limit]
        # per-account rollup + sample subjects for just the shown senders
        addrs = [x["from_addr"] for x in cands]
        if addrs:
            by_acct = self._mail.accounts_by_sender(addrs)
            subj = self._mail.subjects_by_sender(addrs, 3)
            for x in cands:
                x["accounts"] = by_acct.get(x["from_addr"], [])
                x["sample_subjects"] = subj.get(x["from_addr"], [])
        return {"ok": True, "generated": now_iso(),
                "totals": {"candidate_senders": len(cands),
                           "candidate_messages": sum(x["count"] for x in cands)},
                "senders": cands}

    def sender(self, qs):
        """Mandatory-preview detail for ONE sender: authoritative unsubscribe method + the exact INBOX
        messages a sweep would move + a confirm_token that the execute route requires."""
        addr = ((qs.get("addr") or [""])[0]).strip().lower()
        if not addr or "@" not in addr:
            return {"ok": False, "error": "bad address"}
        n, unseen = self._mail.counts(addr)
        if not n:
            return {"ok": False, "error": "no such sender"}
        targets = self._mail.sweep_targets(addr, SWEEP_CAP + 1)
        info, accts = self._unsub.resolve(addr)
        capped = len(targets) > SWEEP_CAP
        affected = [{"pk": t["pk"], "account": t["account"], "folder": t["folder"],
                     "subject": t["subject"], "seen": t["seen"]} for t in targets[:SWEEP_CAP]]
        unsub = {"method": info["method"]}
        if info["method"] == "one-click" or info["method"] == "http-page":
            unsub["https_host"] = urllib.parse.urlparse(info["https_uri"] or "").hostname
        if info["method"] == "mailto":
            unsub["mailto_to"] = info.get("mailto_to")
        return {"ok": True, "from_addr": addr, "count": n, "unseen": unseen,
                "accounts": accts, "domain": addr.rpartition("@")[2],
                "unsub": unsub, "sweep_count": len(affected), "sweep_capped": capped,
                "reversible": {"block": True, "sweep": True, "unsubscribe": False},
                "confirm_token": unsubscribe.confirm_token(self._secret(), addr, info, time.time())}

    def execute(self, body):
        """Commit for ONE sender. ALWAYS-FIRST + reversible: a block rule + verified INBOX sweep to
        Trash (undo_push under one undo_group → a single `z` reverses both). THEN best-effort the chosen
        unsubscribe (irreversible; idempotent via unsub_log). Requires CSRF + strict-origin (route level)
        + a confirm_token echoed from the preview before ANY outbound request fires."""
        from_addr = (body.get("from_addr") or "").strip().lower()
        if not from_addr or "@" not in from_addr:
            return {"ok": False, "error": "bad address"}, 400
        scope = "domain" if body.get("block_scope") == "domain" else "sender"
        do_unsub = bool(body.get("do_unsubscribe"))
        token = str(body.get("confirm_token") or "")
        grp = body.get("undo_group") or ("cleanup:" + secrets.token_hex(16))   # never shared with an earlier cleanup
        block_token = from_addr if scope == "sender" else from_addr.rpartition("@")[2]
        # The unsubscribe is read from the sender's stored mail, so before the sweep: a move deletes
        # the row it moved, and the sender's newest issue is most often one the sweep takes.
        unsub = self._unsub.resolve(from_addr) if do_unsub and unsubscribe.token_in_date(token, time.time()) else None
        token_ok = unsub is not None and unsubscribe.token_names(self._secret(), token, from_addr, unsub[0])

        # 1) BLOCK (reversible) — rule + an unblock descriptor coalesced into the undo group
        self._rules.add(block_token, "block")
        self._filing.undo_push({"kind": "unblock", "token": block_token, "label": "Cleaned up " + from_addr}, grp)

        # 2) SWEEP INBOX copies to Trash (reversible) — each move pushes a 'move' undo under the same group
        targets = self._mail.sweep_targets(from_addr, SWEEP_CAP + 1)
        swept = fail = queued = 0
        for t in targets[:SWEEP_CAP]:
            try:
                res = self._filing.trash(t["pk"], grp)   # one message at a time, as a click on it would
            except Exception:
                res = {}
            if res.get("status") == "queued":
                queued += 1
            elif res.get("ok"):
                swept += 1
            else:
                fail += 1
        self._filing.reclassify(block_token)         # what stays, in other folders, reads as blocked too
        self._filing.stats_changed()

        # 3) UNSUBSCRIBE (irreversible, best-effort) — only with a valid confirm_token; idempotent on
        #    TERMINAL outcomes only (a transient failure/no-method is never latched, so it stays retryable).
        status, http_status, browser_url, method = "skipped", None, None, "none"
        if do_unsub and token_ok:
            status, method, http_status, browser_url = self._unsub.attempt(from_addr, unsub)
        elif do_unsub and not token_ok:
            status = "no_token"                          # outbound refused; block+sweep still stand

        self._audit.cleaned_up(from_addr, block_token, swept + queued, status)
        return {"ok": True, "status": status, "method": method, "blocked_token": block_token,
                "block_scope": scope, "swept": swept + queued, "sweep_failed": fail, "sweep_queued": queued,
                "sweep_capped": len(targets) > SWEEP_CAP, "browser_url": browser_url,
                "http_status": http_status, "undo_group": grp,
                "reversible": {"block": True, "sweep": True, "unsubscribe": False}}, 200
