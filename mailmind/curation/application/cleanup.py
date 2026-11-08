"""Cleaning up after one sender: who is worth it, what it would do, and doing it. Block and sweep are reversible
and stand whatever the unsubscribe does; the unsubscribe can't be taken back, so it wants the preview's token."""
import secrets
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from mailmind.curation.application.ports import AuditLog, FilingCommands, RuleBookRepository, SenderMail
from mailmind.curation.application.unsubscribe import Unsubscribing
from mailmind.curation.domain import cleanup_plan, noise, rulebook, unsubscribe
from mailmind.kernel.errors import NotFound, Refused
from mailmind.kernel.when import now_iso, rel_time
from mailmind.platform.perimeter import tokens

type Query = Mapping[str, Sequence[str]]      # a query string as the routes parse it


class Cleanup:
    def __init__(self, rules: RuleBookRepository, mail: SenderMail, unsubscribing: Unsubscribing,
                 filing: FilingCommands, audit: AuditLog, secret: Callable[[], str]):
        self._rules, self._mail, self._unsub = rules, mail, unsubscribing
        self._filing, self._audit, self._secret = filing, audit, secret

    def reverse_block(self, d: Mapping[str, Any]) -> tuple[bool, str | None, None]:
        """Undo of a block: the rule goes, and what it decided is classified again."""
        try:
            self._rules.remove(d["token"])
            self._filing.reclassify(d["token"])
        except Exception:
            return False, d.get("account"), None
        return True, d.get("account"), None

    def senders(self) -> dict[str, Any]:
        """Ranked cleanup-candidate senders — pure local SQL, no AI, no IMAP, no outbound. Grouped by
        lower(from_addr) across all accounts+folders, bucket-filtered, post-filtered against rules so an
        already-decided sender never reappears."""
        rows = rulebook.undecided(self._mail.noisy_senders(list(noise.BUCKETS), noise.MIN_COUNT), self._rules.sets())
        cands: list[dict[str, Any]] = [
            {"from_addr": c.addr, "from_name": c.name, "domain": c.domain, "count": c.count, "unseen": c.unseen,
             "unseen_ratio": c.unseen_ratio, "last_seen": rel_time(c.last_iso), "bucket": c.bucket, "lu_likely": c.list_likely,
             "noise_score": c.score} for c in noise.ranked(rows, time.time(), noise.LIMIT)]
        # per-account rollup for just the shown senders
        addrs = [x["from_addr"] for x in cands]
        if addrs:
            by_acct = self._mail.accounts_by_sender(addrs)
            for x in cands:
                x["accounts"] = by_acct.get(x["from_addr"], [])
        return {"ok": True, "generated": now_iso(), "senders": cands}

    def sender(self, qs: Query) -> dict[str, Any]:
        """Mandatory-preview detail for ONE sender: authoritative unsubscribe method + the exact INBOX
        messages a sweep would move + a confirm_token that the execute route requires. Raises Refused for an address that
        won't read, and NotFound for a sender with no mail stored."""
        addr = cleanup_plan.sender_named((qs.get("addr") or [""])[0])
        if addr is None:
            raise Refused("bad address")
        n, unseen = self._mail.counts(addr)
        if not n:
            raise NotFound("no such sender")
        targets, capped = cleanup_plan.within_cap(self._mail.sweep_targets(addr, cleanup_plan.to_read()))
        info, accts = self._unsub.resolve(addr)
        affected = [{"pk": t["pk"], "account": t["account"], "folder": t["folder"],
                     "subject": t["subject"], "seen": t["seen"]} for t in targets]
        return {"ok": True, "from_addr": addr, "count": n, "unseen": unseen,
                "accounts": accts, "domain": rulebook.domain_of(addr),
                "unsub": unsubscribe.preview(info), "sweep_count": len(affected), "sweep_capped": capped,
                "reversible": cleanup_plan.REVERSIBLE,
                "confirm_token": tokens.mint(self._secret(), unsubscribe.TOKEN_PURPOSE, unsubscribe.preview_said(addr, info), time.time())}

    def _batch_sweeps(self, addrs: Sequence[str]) -> tuple[list[tuple[str, Sequence[Any]]], bool]:
        """What a batch of these senders would sweep of each, and whether any of it is left behind."""
        read = [(a, *cleanup_plan.within_cap(self._mail.sweep_targets(a, cleanup_plan.to_read()))) for a in addrs]
        return cleanup_plan.within_batch(read)

    def batch_preview(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """What cleaning up these senders together would do, before anyone says yes: each one's mail and how many of its INBOX
        copies go. Raises Refused for a list that won't read and NotFound when none of them has mail stored."""
        addrs = cleanup_plan.senders_named(body.get("senders"))
        if addrs is None:
            raise Refused("bad senders")
        sweeps, capped = self._batch_sweeps(addrs)
        senders: list[dict[str, Any]] = []
        for addr, targets in sweeps:
            n, unseen = self._mail.counts(addr)
            if n:
                senders.append({"from_addr": addr, "count": n, "unseen": unseen, "sweep_count": len(targets)})
        if not senders:
            raise NotFound("no such sender")
        return {"ok": True, "senders": senders, "sweep_count": sum(s["sweep_count"] for s in senders), "sweep_capped": capped,
                "reversible": cleanup_plan.REVERSIBLE}

    def batch_execute(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Commit for MANY senders: each one's block, then every sweep, under one undo group, so a single `z` reverses the whole
        batch. Only the reversible half: an unsubscribe can't be taken back, and each wants its own preview's token."""
        addrs = cleanup_plan.senders_named(body.get("senders"))
        if addrs is None:
            raise Refused("bad senders")
        grp = body.get("undo_group") or ("cleanup:" + secrets.token_hex(16))        # never shared with an earlier cleanup
        sweeps, capped = self._batch_sweeps(addrs)
        for addr, _targets in sweeps:
            self._rules.add(addr, "block")
            self._filing.undo_push({"kind": "unblock", "token": addr, "label": f"Cleaned up {len(addrs)} senders"}, grp)
        sender_of = {(t["message_key"], t["folder"], t["uid"]): addr for addr, targets in sweeps for t in targets
                     if t["message_key"] is not None}
        said = self._sweep_many(sender_of, grp)
        for addr, _targets in sweeps:
            self._filing.reclassify(addr)            # what stays, in other folders, reads as blocked too
        self._filing.stats_changed()
        swept_of = Counter(sender_of[(f["key"], f["folder"], f["uid"])] for f in said["filed"] if (f["key"], f["folder"], f["uid"]) in sender_of)
        for addr, _targets in sweeps:
            self._audit.cleaned_up(addr, addr, swept_of[addr], "skipped")
        return {"ok": True, "blocked": addrs, "swept": said["count"], "sweep_failed": said["failed"], "sweep_queued": said["queued"],
                "sweep_capped": capped, "undo_group": grp, "reversible": cleanup_plan.REVERSIBLE}

    def _sweep_many(self, sender_of: Mapping[tuple[int, str, int], str], grp: str) -> Mapping[str, Any]:
        """Trash these copies as filing marked rows does: one transaction accepts them all under `grp`, then each is tried."""
        if not sender_of:
            return {"count": 0, "failed": 0, "queued": 0, "filed": []}
        try:
            return self._filing.trash_many([{"key": k, "folder": f, "uid": u} for k, f, u in sender_of], grp)
        except Exception:                            # none of them went: each is where it was
            return {"count": 0, "failed": len(sender_of), "queued": 0, "filed": []}

    def _trash(self, pk: int, grp: str) -> Mapping[str, Any]:
        try:
            return self._filing.trash(pk, grp)   # one message at a time, as a click on it would
        except Exception:
            return {}

    def execute(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Commit for ONE sender. ALWAYS-FIRST + reversible: a block rule + verified INBOX sweep to
        Trash (undo_push under one undo_group → a single `z` reverses both). THEN best-effort the chosen
        unsubscribe (irreversible; idempotent via unsub_log). Requires CSRF + strict-origin (route level)
        + a confirm_token echoed from the preview before ANY outbound request fires."""
        plan = cleanup_plan.plan_from(body)
        if plan is None:
            raise Refused("bad address")
        from_addr, do_unsub, token, block_token = plan.from_addr, plan.do_unsubscribe, plan.confirm_token, plan.block_token
        grp = body.get("undo_group") or ("cleanup:" + secrets.token_hex(16))   # never shared with an earlier cleanup
        # The unsubscribe is read from the sender's stored mail, so before the sweep: a move deletes
        # the row it moved, and the sender's newest issue is most often one the sweep takes.
        unsub = self._unsub.resolve(from_addr) if do_unsub and tokens.in_date(token, time.time(), unsubscribe.TOKEN_LIFE) else None
        token_ok = unsub is not None and tokens.names(self._secret(), token, unsubscribe.TOKEN_PURPOSE, unsubscribe.preview_said(from_addr, unsub[0]))

        # 1) BLOCK (reversible) — rule + an unblock descriptor coalesced into the undo group
        self._rules.add(block_token, "block")
        self._filing.undo_push({"kind": "unblock", "token": block_token, "label": "Cleaned up " + from_addr}, grp)

        # 2) SWEEP INBOX copies to Trash (reversible) — each move pushes a 'move' undo under the same group
        targets, capped = cleanup_plan.within_cap(self._mail.sweep_targets(from_addr, cleanup_plan.to_read()))
        swept = cleanup_plan.tally(self._trash(t["pk"], grp) for t in targets)
        self._filing.reclassify(block_token)         # what stays, in other folders, reads as blocked too
        self._filing.stats_changed()

        # 3) UNSUBSCRIBE (irreversible, best-effort) — only with a valid confirm_token; idempotent on
        #    TERMINAL outcomes only (a transient failure/no-method is never latched, so it stays retryable).
        tried = self._unsub.attempt(from_addr, unsub) if unsub is not None and token_ok else unsubscribe.not_tried(do_unsub)

        self._audit.cleaned_up(from_addr, block_token, swept.swept, tried.status)
        return {"ok": True, "status": tried.status, "method": tried.method, "blocked_token": block_token,
                "block_scope": plan.scope, "swept": swept.swept, "sweep_failed": swept.failed, "sweep_queued": swept.queued,
                "sweep_capped": capped, "browser_url": tried.browser_url,
                "http_status": tried.http_status, "undo_group": grp,
                "reversible": cleanup_plan.REVERSIBLE}
