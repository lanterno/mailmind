"""One tool to triage every mailbox (Gmail + Proton) from a single process —
replaces the per-account scripts and the ad-hoc /tmp sweeps.

Fetches INBOX headers live, classifies with the unified mailclass, and moves
junk to Trash with VERIFICATION (re-checks that each UID actually left the
INBOX, retrying stragglers — the fix for the silent stale-UID move glitch).

Each of these is a `python -m mailmind` command, which says how to run it.

'all' covers every connected account. Default is 'all'.
Every sweep appends what it trashed to sweep_audit.jsonl (account, sender, subject, reason).
"""
import sys
import os
import json
import email
import time
import imaplib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import accounts
from mailmind.bootstrap import curation
from mailmind.content import mime
from mailmind.content.mime import dec
import pool
from mailmind.bootstrap.curation import rules
from mailmind.accounts.domain import provider
from mailmind.bootstrap import mailbox
from mailmind.platform import settings
from mailmind.curation.domain import rulebook

MOVE_BATCH = 200
HDR_FIELDS = ("FROM", "SUBJECT", "MESSAGE-ID", "LIST-UNSUBSCRIBE", "LIST-ID")
EXP_FIELDS = ("FROM", "SUBJECT", "MESSAGE-ID", "DATE")


def fetch_headers(s):
    """Return list of {uid, from, name, subject, bulk} for the INBOX, which `s` selects."""
    s.select("INBOX")
    rows = []
    for h in s.headers(s.uids("ALL"), HDR_FIELDS):
        msg = email.message_from_bytes(h.raw)
        name, addr = mime.sender_of(msg)
        rows.append({"uid": str(h.uid), "from": addr.lower(), "name": dec(name),
                     "subject": dec(msg.get("Subject", "")),
                     "mid": (msg.get("Message-ID", "") or "").strip(),
                     "bulk": bool(msg.get("List-Unsubscribe") or msg.get("List-ID"))})
    return rows


def classify_rows(rows):
    for r in rows:
        r["bucket"], r["conf"], r["reason"] = curation.classify(
            r["from"], r["subject"], r["name"], r["bulk"])
    return rows


def audit(account, rows):
    """Append a reviewable record of everything a sweep trashed."""
    stamp = datetime.now(timezone.utc).isoformat()
    with open(settings.current().sweep_audit, "a") as f:
        for r in rows:
            f.write(json.dumps({"kind": "sweep", "ts": stamp, "account": account,
                                "uid": r["uid"], "from": r["from"],
                                "subject": r["subject"][:140],
                                "bucket": r["bucket"], "reason": r["reason"]}) + "\n")


def plan_account(acct):
    """Connect, classify the live INBOX, and RETURN structured per-bucket stats
    (no printing) — the shape /api/stats serves. Errors are captured, not raised."""
    res = {"account": acct["email"], "kind": acct["kind"], "in_inbox": 0,
           "by_bucket": {}, "junk": 0, "keep": 0, "top_senders": {}, "error": None}
    try:
        with pool.borrow(acct) as s:      # pooled read: reuse the login (no fresh connect per /api/stats)
            rows = classify_rows(fetch_headers(s))   # ctx-mgr ALWAYS closes → no socket leak on fetch error
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
        return res
    by = defaultdict(list)
    for r in rows:
        by[r["bucket"]].append(r)
    res["in_inbox"] = len(rows)
    res["by_bucket"] = {b: len(rs) for b, rs in by.items()}
    res["junk"] = sum(len(by[b]) for b in curation.JUNK_BUCKETS)
    res["keep"] = len(rows) - res["junk"]
    res["top_senders"] = {b: [s for s, _ in Counter(r["from"] for r in rs).most_common(4)]
                          for b, rs in by.items()}
    res["_rows"] = rows  # internal: used by cmd_plan/sweep, dropped before serialization
    return res


def plan_account_db(acct):
    """DB-sourced twin of plan_account — NO IMAP, instant. Re-classifies the STORED INBOX rows with
    the same mailclass (so it reflects current keep/block rules and matches what a live sweep would
    move). Accurate because auto-reconcile keeps the stored INBOX in sync with the server. Used by the
    Curate drawer (/api/stats), which was a ~3s live 8-account header scan; the Sweep buttons still
    re-verify live before moving anything."""
    import store
    res = {"account": acct["email"], "kind": acct["kind"], "in_inbox": 0,
           "by_bucket": {}, "junk": 0, "keep": 0, "top_senders": {}, "error": None}
    c = store.conn()
    rows = c.execute("""SELECT from_addr, from_name, subject, bucket, list_unsub_raw FROM messages
                        WHERE account=? AND folder='INBOX' AND deleted=0""", (acct["email"],)).fetchall()
    c.close()
    by = defaultdict(list)
    for r in rows:
        addr = (r["from_addr"] or "").lower()
        # The stored row keeps no List header, and a sweep reads them live. What ingest saw in them
        # shows in the bucket: the classifier's rules come first, so a rule made since still wins.
        listed = r["bucket"] == curation.MKT or bool(r["list_unsub_raw"])
        by[curation.classify(addr, r["subject"] or "", r["from_name"] or "", listed)[0]].append(addr)
    res["in_inbox"] = len(rows)
    res["by_bucket"] = {b: len(v) for b, v in by.items()}
    res["junk"] = sum(len(by[b]) for b in curation.JUNK_BUCKETS)
    res["keep"] = len(rows) - res["junk"]
    res["top_senders"] = {b: [s for s, _ in Counter(v).most_common(4)] for b, v in by.items()}
    return res


def _move_one(s, uid, dest):
    """One message: MOVE, or on a server without it a checked COPY, then \\Deleted and a UID
    EXPUNGE of this UID alone. A failure leaves it for the verification pass."""
    try:
        s.move([uid], dest)
    except imaplib.IMAP4.abort:
        raise
    except imaplib.IMAP4.error:
        pass


def _still_in_inbox(s):
    s.select("INBOX")
    return {str(u) for u in s.uids("ALL")}


def move_verified(s, uids, dest):
    """Move uids (strings) to dest, then re-check INBOX and retry any that didn't leave.
    Returns (moved, stragglers). They go MOVE_BATCH at a time, in one MOVE where the server offers
    it, and copied together where it doesn't; a refused batch goes one message at a time
    (_move_one), so one bad UID can't sink the rest."""
    s.select("INBOX", readonly=False)
    for i in range(0, len(uids), MOVE_BATCH):
        chunk = uids[i:i + MOVE_BATCH]
        try:
            s.move(chunk, dest)
        except imaplib.IMAP4.abort:
            raise
        except imaplib.IMAP4.error:
            for u in chunk:
                _move_one(s, u, dest)
        time.sleep(0.2)
    # verification: which requested uids are STILL in INBOX?
    want = set(uids)
    present = _still_in_inbox(s)
    remaining = [u for u in uids if u in present and u in want]
    if remaining:  # one retry pass
        s.select("INBOX", readonly=False)
        for u in remaining:
            _move_one(s, u, dest)
        present = _still_in_inbox(s)
        remaining = [u for u in remaining if u in present]
    return len(uids) - len(remaining), remaining


def _count(s, folder):
    try:
        return s.select(folder).exists
    except imaplib.IMAP4.abort:
        raise
    except imaplib.IMAP4.error:
        return 0


def cmd_status(accts):
    for a in accts:
        try:
            s = mailbox.open_session(a)
            inbox = _count(s, "INBOX")
            trash = _count(s, accounts.trash_folder(a))
            print(f"{a['email']:30} INBOX {inbox:>6}   Trash {trash:>6}")
            s.logout()
        except Exception as e:
            print(f"{a['email']:30} ERROR: {type(e).__name__}: {e}")


def cmd_inventory(accts):
    """List every folder with message/unseen counts, for any account type."""
    for a in accts:
        try:
            s = mailbox.open_session(a)
        except Exception as e:
            print(f"\n=== {a['email']} ===\n   ERROR: {type(e).__name__}: {e}")
            continue
        print(f"\n=== {a['email']} ({a['kind']}) ===")
        try:
            for name in [f.name for f in s.folders()]:
                try:
                    uns, msgs = s.status(name)
                    print(f"   {name:<26} {msgs:>7}  unseen {uns}")
                except Exception:
                    print(f"   {name:<26} {'(not selectable)':>7}")
        finally:
            try:
                s.logout()
            except Exception:
                pass


def _prune_moved(acct, moved, stragglers):
    """The local rows of what a sweep moved, found by Message-ID: a UIDVALIDITY change no sync has
    seen yet leaves that good, where a UID may be another row's. A message with none goes by UID."""
    import store
    left = set(stragglers)
    c = store.conn()
    try:
        for r in moved:
            if r["uid"] in left:
                continue
            if not r["mid"]:
                store.delete_by_uids(c, acct["email"], "INBOX", [r["uid"]])
                continue
            row = c.execute("SELECT pk FROM messages WHERE account=? AND folder='INBOX' AND message_id=?",
                            (acct["email"], r["mid"])).fetchone()
            if row:
                store.delete_message(c, row["pk"])
        c.commit()
    finally:
        c.close()


def sweep_account(acct, dry=False):
    """Dry-run plan or verified move of an account's junk buckets to Trash.
    Prunes matching local DB rows on a real run. Returns a result dict."""
    p = plan_account(acct)
    if p["error"]:
        return {"account": acct["email"], "error": p["error"], "planned": 0}
    junk_rows = [r for r in p["_rows"] if r["bucket"] in curation.JUNK_BUCKETS]
    out = {"account": acct["email"], "planned": len(junk_rows), "by_bucket": p["by_bucket"],
           "senders": len(set(r["from"] for r in junk_rows)), "moved": 0,
           "stragglers": [], "error": None}
    if dry or not junk_rows:
        return out
    audit(acct["email"], junk_rows)  # log BEFORE moving so a crash still records intent
    try:
        s = mailbox.open_session(acct)
        try:
            moved, stragglers = move_verified(s, [r["uid"] for r in junk_rows], accounts.trash_folder(acct))
        finally:
            try:
                s.logout()
            except Exception:
                pass
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    out["moved"], out["stragglers"] = moved, stragglers
    _prune_moved(acct, junk_rows, stragglers)        # don't re-ghost swept mail
    return out


def sweep_account_ai(acct, dry=False):
    """AI-verdict sweep: trash INBOX messages MailMind flagged is_junk=1 (the AI
    classifier that reads the body — catches header-less newsletters the mailclass
    header sweep misses). Matched to the LIVE inbox by Message-ID so UIDs are always
    current (no UIDVALIDITY risk), minus any keep-ruled sender/domain. Move to Trash
    (reversible), verified + audited; prune the local DB by Message-ID."""
    import store
    r = rules.load()
    keep_senders, keep_domains = set(r["keep_senders"]), set(r["keep_domains"])

    def _keep_ruled(fa):
        fa = (fa or "").lower(); dom = fa.partition("@")[2]
        return fa in keep_senders or any(dom == d or dom.endswith("." + d) for d in keep_domains)

    c = store.conn()
    junk_mids = set(m for (m,) in c.execute(
        """SELECT message_id FROM messages WHERE account=? AND folder='INBOX' AND is_junk=1
           AND deleted=0 AND (snoozed_until IS NULL OR snoozed_until=0)
           AND message_id IS NOT NULL AND message_id!=''""", (acct["email"],)).fetchall())
    c.close()
    out = {"account": acct["email"], "planned": 0, "moved": 0, "stragglers": [],
           "kept_by_rule": 0, "error": None}
    if not junk_mids:
        return out
    try:
        s = mailbox.open_session(acct)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    try:                                    # single try/finally so s is ALWAYS logged out (no socket leak)
        live = fetch_headers(s)
        targets, kept = [], 0
        for h in live:
            if h.get("mid") and h["mid"] in junk_mids:
                if _keep_ruled(h["from"]):
                    kept += 1
                else:
                    targets.append(h)
        out["planned"], out["kept_by_rule"] = len(targets), kept
        if dry or not targets:
            return out
        audit(acct["email"], [{"uid": t["uid"], "from": t["from"], "subject": t["subject"],
                               "bucket": "ai-junk", "reason": "is_junk (AI verdict)"} for t in targets])
        moved, stragglers = move_verified(s, [t["uid"] for t in targets], accounts.trash_folder(acct))
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    finally:
        try:
            s.logout()
        except Exception:
            pass
    out["moved"], out["stragglers"] = moved, stragglers
    _prune_moved(acct, targets, stragglers)
    return out


def _arrival_ts(arrived, msg):
    """Epoch seconds a message arrived: IMAP INTERNALDATE (`arrived`, a datetime), else its Date
    header, else None (an unknown age never expires)."""
    if arrived is not None:
        return arrived.timestamp()
    try:
        return parsedate_to_datetime(msg.get("Date", "")).timestamp()
    except (TypeError, ValueError, IndexError):
        return None


def select_expired(rows, expire, now):
    """Rows whose sender has an expiry rule and whose arrival is older than it. Pure — no IMAP.
    Tags each hit with r['hours']."""
    out = []
    for r in rows:
        hours = rulebook.expiry_hours(r["from"], expire)
        if hours and r.get("ts") is not None and now - r["ts"] > hours * 3600:
            r["hours"] = hours
            out.append(r)
    return out


def expire_account(acct, dry=False, now=None):
    """Move INBOX mail past its expiry rule (rules.EXPIRE) to Trash — verified, audited, and
    pruned from the local DB by Message-ID. Ages come from the live server, so this is right
    whether or not the store is synced. Errors are captured, not raised."""
    import store
    expire = rules.load()[rules.EXPIRE]
    out = {"account": acct["email"], "planned": 0, "moved": 0, "stragglers": [], "error": None}
    if not expire:
        return out
    try:
        s = mailbox.open_session(acct)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    try:                                    # single try/finally so s is ALWAYS logged out (no socket leak)
        s.select("INBOX")
        uids = set()
        for token in expire:                # coarse server-side filter; select_expired matches exactly
            try:
                uids.update(s.uids("FROM", accounts.imap_quote(token)))
            except imaplib.IMAP4.abort:
                raise
            except imaplib.IMAP4.error:     # a refused search finds nothing
                pass
        rows = []
        for h in s.headers(sorted(uids), EXP_FIELDS, internaldate=True):
            msg = email.message_from_bytes(h.raw)
            rows.append({"uid": str(h.uid), "from": mime.sender_of(msg)[1].lower(),
                         "subject": dec(msg.get("Subject", "")),
                         "mid": (msg.get("Message-ID", "") or "").strip(),
                         "ts": _arrival_ts(h.internaldate, msg)})
        targets = select_expired(rows, expire, now if now is not None else time.time())
        out["planned"] = len(targets)
        if dry or not targets:
            return out
        for t in targets:
            t["bucket"], t["reason"] = "expired", f"expire rule: older than {t['hours']}h"
        audit(acct["email"], targets)       # log BEFORE moving so a crash still records intent
        moved, stragglers = move_verified(s, [t["uid"] for t in targets], accounts.trash_folder(acct))
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    finally:
        try:
            s.logout()
        except Exception:
            pass
    out["moved"], out["stragglers"] = moved, stragglers
    strag = set(stragglers)
    c = store.conn()
    for t in targets:                       # prune by Message-ID (pk is not stable)
        if t["uid"] in strag or not t["mid"]:
            continue
        row = c.execute("SELECT pk FROM messages WHERE account=? AND folder='INBOX' AND message_id=?",
                        (acct["email"], t["mid"])).fetchone()
        if row:
            store.delete_message(c, row["pk"])
    c.commit()
    c.close()
    return out


def cmd_expire(accts, dry=False):
    expire = rules.load()[rules.EXPIRE]
    if not expire:
        print("no expiry rules — add one with: python -m mailmind rules expire <sender|domain> <hours>")
        return
    print("rules: " + ", ".join(f"{t} {h}h" for t, h in expire.items()))
    grand_p = grand_m = 0
    for a in accts:
        r = expire_account(a, dry=dry)
        if r["error"]:
            print(f"{a['email']:38} ERROR: {r['error']}")
            continue
        tag = f"would trash {r['planned']}" if dry else f"moved {r['moved']}/{r['planned']}"
        strag = f"  !{len(r['stragglers'])} stragglers" if r.get("stragglers") else ""
        print(f"{a['email']:38} {tag:>16}{strag}")
        grand_p += r["planned"]; grand_m += r.get("moved", 0)
    print(f"\nTOTAL: expired {grand_p}" + ("" if dry else f" · moved {grand_m}"))


def cmd_sweep_ai(accts, dry=False):
    grand_p = grand_m = grand_k = 0
    for a in accts:
        r = sweep_account_ai(a, dry=dry)
        if r["error"]:
            print(f"{a['email']:38} ERROR: {r['error']}")
            continue
        tag = f"would trash {r['planned']}" if dry else f"moved {r['moved']}/{r['planned']}"
        strag = f"  !{len(r['stragglers'])} stragglers" if r.get("stragglers") else ""
        print(f"{a['email']:38} {tag:>16}   (kept by rule: {r['kept_by_rule']}){strag}")
        grand_p += r["planned"]; grand_m += r.get("moved", 0); grand_k += r["kept_by_rule"]
    print(f"\nTOTAL: planned {grand_p}" + ("" if dry else f" · moved {grand_m}") + f" · kept-by-rule {grand_k}")


def cmd_plan(accts, do_sweep=False, dry=False):
    grand = 0
    for a in accts:
        p = plan_account(a)
        if p["error"]:
            print(f"{a['email']:30} ERROR: {p['error']}")
            continue
        print(f"\n=== {a['email']}  ({p['in_inbox']} in INBOX) ===")
        for k in [curation.SPAM, curation.BLOCKED, curation.MKT, curation.NOTIF,
                  curation.RECEIPT, curation.PROTECTED, curation.PERSONAL]:
            n = p["by_bucket"].get(k, 0)
            if n:
                tag = " TRASH" if k in curation.JUNK_BUCKETS else " keep"
                print(f"   {k:<24} {n:>5}{tag}   {', '.join(p['top_senders'].get(k, []))}")
        print(f"   -> would trash {p['junk']} / keep {p['keep']}")
        grand += p["junk"]
        if do_sweep and not dry and p["junk"]:
            r = sweep_account(a, dry=False)
            msg = f"   swept: moved {r['moved']}/{p['junk']} to Trash (logged -> sweep_audit.jsonl)"
            if r["stragglers"]:
                msg += f"  ! {len(r['stragglers'])} could not be verified-moved"
            print(msg)
    print(f"\nTOTAL junk across accounts: {grand}")


def cmd_empty_trash(accts):
    for a in accts:
        if provider.resolve(a).bulk_expunge_unsafe:
            print(f"{a['email']:30} SKIPPED — empty Proton Trash with the native button "
                  "(Bridge crashes on bulk expunge).")
            continue
        try:
            s = mailbox.open_session(a)
            tf = accounts.trash_folder(a)          # [Gmail]/Trash for gmail, "Trash" for plain IMAP
            s.select(tf, readonly=False)
            uids = s.uids("ALL")
            if not uids:
                print(f"{a['email']:30} Trash already empty")
                s.logout()
                continue
            try:
                for i in range(0, len(uids), 500):
                    s.add_flags(uids[i:i + 500], ["\\Deleted"])
                    s.expunge(uids[i:i + 500])     # these UIDs alone, so nothing another client flagged
            except imaplib.IMAP4.abort:
                pass  # Gmail sometimes drops the connection mid-expunge but completes it
            print(f"{a['email']:30} purged {len(uids)} from Trash")
            try:
                s.logout()
            except Exception:
                pass
        except Exception as e:
            print(f"{a['email']:30} ERROR: {type(e).__name__}: {e}")
