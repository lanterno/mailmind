"""What a sweep would take, worked out from mail already read: how an inbox falls into buckets, which of it is junk and what
a sweep of what's stored as junk may reach. Moving it, verified and audited, is the use case's."""
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable
from typing import Any, TypedDict

from mailmind.curation.domain import classifier
from mailmind.curation.domain.classifier import RuleSets
from mailmind.curation.domain.live import Audited, Classified, Read, held
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


def listed(row: Row) -> bool:
    """Whether a stored row came from a list, as a sweep reads its List headers live. The row keeps no List header: what
    ingest saw in them shows in the bucket it gave, or in the way out it kept."""
    return row["bucket"] == classifier.MKT or bool(row["list_unsub_raw"])


def stored_bucket(row: Row, rules: RuleSets) -> tuple[str, str]:
    """(bucket, sender) of a stored row, as a live sweep would judge it: the classifier's rules come first, so a rule made
    since ingest still wins."""
    addr = (row["from_addr"] or "").lower()
    return classifier.classify(addr, row["subject"] or "", row["from_name"] or "", listed(row), rules=rules)[0], addr


def read_from_stored(row: Row) -> Read:
    """A message of the INBOX as last stored, as a sweep reads it off the server: its List headers as ingest saw them
    (`listed`), and its flags as the last sync left them, so one stored with no flag that says it was seen is unread."""
    return {"uid": str(row["uid"]), "from": (row["from_addr"] or "").lower(), "name": row["from_name"] or "",
            "subject": row["subject"] or "", "mid": (row["message_id"] or "").strip(), "bulk": listed(row),
            "starred": bool(row["flagged"]), "unread": not row["seen"]}


def sweepable(rows: Iterable[Row], judged: Iterable[tuple[str, str]]) -> int:
    """How much of a stored inbox a sweep would move now, from its rows and each one's (bucket, sender): its junk that
    someone has read and no one starred."""
    return sum(1 for r, (bucket, _) in zip(rows, judged)
               if bucket in classifier.JUNK_BUCKETS and r["seen"] and not r["flagged"])


def junk(rows: Iterable[Classified]) -> list[Classified]:
    """What a sweep takes of what it classified."""
    return [r for r in rows if r["bucket"] in classifier.JUNK_BUCKETS]


def within_cap[R: Read](junk: list[R]) -> tuple[list[R], bool]:
    """What a sweep takes of `junk`: the newest SWEEP_MAX by UID, in the order the INBOX holds them, and whether that leaves
    any for the next sweep."""
    if len(junk) <= SWEEP_MAX:
        return junk, False
    newest = sorted(junk, key=lambda r: int(r["uid"]), reverse=True)[:SWEEP_MAX]
    return sorted(newest, key=lambda r: int(r["uid"])), True


def since_stored[R: Read](live: list[R], stored: Iterable[Row]) -> list[R]:
    """Of the INBOX read off the server, what a plan from the stored INBOX could have named: its oldest stored message and
    all since. Older mail was never stored, as a sync that read only recent mail leaves it (a folder taken as read whole
    before migration 24, or `mailmind sync`'s last N days), so that plan never named it, and the sweep after it leaves it.
    With nothing stored, it named nothing. Mail since may still go: read elsewhere, or come, since the last sync."""
    oldest = min((int(r["uid"]) for r in stored), default=None)
    return [] if oldest is None else [r for r in live if int(r["uid"]) >= oldest]


def planned(account: str, rows: list[Classified]) -> tuple[dict[str, Any], list[Classified]]:
    """A sweep's answer before anything moves, from the INBOX as `rows` show it, and what it takes: its junk that someone
    has read and no one starred, the newest SWEEP_MAX. `capped` says some is left for the next sweep."""
    junk_rows = junk(rows)
    free = [r for r in junk_rows if not held(r)]      # starred or unread junk stays, whatever it is
    taken, capped = within_cap(free)
    return {"account": account, "planned": len(taken),
            "by_bucket": tally((r["bucket"], r["from"]) for r in rows)["by_bucket"],
            "senders": len({r["from"] for r in taken}), "going": going(taken), "held_back": len(junk_rows) - len(free),
            "capped": capped,
            # Undo finds a message in Trash by its Message-ID: one with none goes, but can't come back.
            "no_undo": sum(1 for r in taken if not r["mid"]),
            "moved": 0, "stragglers": [], "error": None}, taken


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
