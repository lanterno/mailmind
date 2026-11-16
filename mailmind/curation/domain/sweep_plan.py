"""What a sweep would take, worked out from mail already read: how an inbox falls into buckets, which of it is junk and what
a sweep of what's stored as junk may reach. Moving it, verified and audited, is the use case's."""
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable
from typing import Any, TypedDict

from mailmind.curation.domain import classifier
from mailmind.curation.domain.classifier import RuleSets
from mailmind.curation.domain.live import Audited, Classified, Read
from mailmind.curation.domain.rulebook import keep_ruled
from mailmind.kernel.row import Row

TOP_SENDERS = 4
SWEEP_MAX = 500     # the most of an account's junk one sweep moves: Undo puts each back on its own, and a click waits on it
LISTED = 5          # how many of a bucket's senders, and of its subjects, a sweep's plan names before it asks
JUNK_ORDER = (classifier.SPAM, classifier.BLOCKED, classifier.MKT, classifier.NOTIF)    # the junk buckets, as Curate lists them


class Tally(TypedDict):
    in_inbox: int
    by_bucket: dict[str, int]
    junk: int
    keep: int
    top_senders: dict[str, list[str]]


def tally(mail: Iterable[tuple[str, str]]) -> Tally:
    """How an inbox falls, from each message's (bucket, sender). `by_bucket` names the buckets holding mail. `top_senders`
    names each of those, and every junk bucket too, with nobody in one that holds none."""
    by: defaultdict[str, list[str]] = defaultdict(list)
    for bucket, sender in mail:
        by[bucket].append(sender)
    in_inbox = sum(len(senders) for senders in by.values())
    by_bucket = {b: len(senders) for b, senders in by.items()}
    junk_count = sum(len(by[b]) for b in classifier.JUNK_BUCKETS)
    return {"in_inbox": in_inbox, "by_bucket": by_bucket, "junk": junk_count, "keep": in_inbox - junk_count,
            "top_senders": {b: [s for s, _ in Counter(senders).most_common(TOP_SENDERS)] for b, senders in by.items()}}


def classified(rows: Iterable[Read], rules: RuleSets) -> list[Classified]:
    """What the classifier makes of mail read live."""
    out: list[Classified] = []
    for r in rows:
        bucket, conf, reason = classifier.classify(r["from"], r["subject"], r["name"], r["bulk"], rules=rules)
        out.append({**r, "bucket": bucket, "conf": conf, "reason": reason})
    return out


def stored_bucket(row: Row, rules: RuleSets) -> tuple[str, str]:
    """(bucket, sender) of a stored row, as a live sweep would judge it. The row keeps no List header, and a sweep reads them
    live. What ingest saw in them shows in the bucket: the classifier's rules come first, so a rule made since still wins."""
    addr = (row["from_addr"] or "").lower()
    listed = row["bucket"] == classifier.MKT or bool(row["list_unsub_raw"])
    return classifier.classify(addr, row["subject"] or "", row["from_name"] or "", listed, rules=rules)[0], addr


def junk(rows: Iterable[Classified]) -> list[Classified]:
    """What a sweep takes of what it classified."""
    return [r for r in rows if r["bucket"] in classifier.JUNK_BUCKETS]


def within_cap(junk: list[Classified]) -> tuple[list[Classified], bool]:
    """What a sweep takes of `junk`: the newest SWEEP_MAX by UID, in the order the INBOX holds them, and whether that leaves
    any for the next sweep."""
    if len(junk) <= SWEEP_MAX:
        return junk, False
    newest = sorted(junk, key=lambda r: int(r["uid"]), reverse=True)[:SWEEP_MAX]
    return sorted(newest, key=lambda r: int(r["uid"])), True


def going(junk: Iterable[Classified]) -> list[dict[str, Any]]:
    """What goes, a junk bucket at a time in JUNK_ORDER, each that holds any: how many, the LISTED senders with the most of
    it (then by address) and how many each, and its newest LISTED subjects, none twice."""
    by: defaultdict[str, list[Classified]] = defaultdict(list)
    for r in junk:
        by[r["bucket"]].append(r)
    out: list[dict[str, Any]] = []
    for bucket in JUNK_ORDER:
        if not (mail := by.get(bucket)):
            continue
        counts = Counter(r["from"] for r in mail)
        senders = sorted(counts.items(), key=lambda s: (-s[1], s[0]))[:LISTED]
        subjects: list[str] = []
        for r in sorted(mail, key=lambda r: int(r["uid"]), reverse=True):
            if len(subjects) == LISTED:
                break
            if r["subject"] not in subjects:
                subjects.append(r["subject"])
        out.append({"bucket": bucket, "count": len(mail),
                    "senders": [{"from_addr": addr, "count": n} for addr, n in senders], "subjects": subjects})
    return out


def stored_targets(live: Iterable[Read], junk_ids: Collection[str], rules: RuleSets) -> tuple[list[Read], int]:
    """(what sweep-stored takes of the live inbox, how many a keep rule held back). It matches by Message-ID, so a UID is
    always current, and mail with none is out of its reach."""
    targets: list[Read] = []
    kept = 0
    for h in live:
        if h.get("mid") and h["mid"] in junk_ids:
            if keep_ruled(h["from"], rules):
                kept += 1
            else:
                targets.append(h)
    return targets, kept


def stored_audited(targets: Iterable[Read]) -> list[Audited]:
    """The audit's line for each message a sweep of stored junk takes."""
    return [{"uid": t["uid"], "from": t["from"], "subject": t["subject"], "bucket": "stored-junk", "reason": "is_junk (the classifier, at ingest)"}
            for t in targets]
