"""One tool to triage every mailbox (Gmail + Proton) from a single process —
replaces the per-account scripts and the ad-hoc /tmp sweeps.

Fetches INBOX headers live, classifies with the unified mailclass, and moves
junk to Trash with VERIFICATION (re-checks that each UID actually left the
INBOX, retrying stragglers — the fix for the silent stale-UID move glitch).

Usage:
  python3 manage.py status [email|all]      # INBOX + Trash counts
  python3 manage.py inventory [email|all]   # every folder + message/unseen counts
  python3 manage.py plan   [email|all]      # classify + show what would be trashed
  python3 manage.py sweep  [email|all] [--dry]   # move junk buckets to Trash (verified + audited)
  python3 manage.py empty-trash [email|all]      # Gmail only (Proton refused — use native button)
  python3 manage.py keep   <sender|domain> ...   # always keep (learned rule)
  python3 manage.py block  <sender|domain> ...   # always trash (learned rule)
  python3 manage.py rules                        # show the learned allow/block lists

'all' covers every connected account. Default is 'all'.
Every sweep appends what it trashed to sweep_audit.jsonl (account, sender, subject, reason).
"""
import sys
import os
import re
import json
import email
import time
from datetime import datetime, timezone
from email.header import decode_header
from email.utils import parseaddr
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import accounts
import mailclass
import rules

AUDIT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sweep_audit.jsonl")

HDR_BATCH = 300
MOVE_BATCH = 200
HDR_FIELDS = "(UID BODY.PEEK[HEADER.FIELDS (FROM SUBJECT LIST-UNSUBSCRIBE LIST-ID)])"


def dec(s):
    if not s:
        return ""
    out = []
    for part, enc in decode_header(s):
        if isinstance(part, bytes):
            try:
                out.append(part.decode(enc or "utf-8", "replace"))
            except (LookupError, Exception):
                out.append(part.decode("utf-8", "replace"))
        else:
            out.append(part)
    return "".join(out).replace("\n", " ").replace("\r", " ").strip()


def _uid(meta):
    import re
    m = re.search(r"UID (\d+)", meta.decode("utf-8", "replace"))
    return m.group(1) if m else None


def fetch_headers(M):
    """Return list of {uid, from, name, subject, bulk} for the selected INBOX."""
    M.select('"INBOX"', readonly=True)
    typ, d = M.uid("SEARCH", None, "ALL")
    ids = d[0].split()
    rows = []
    for i in range(0, len(ids), HDR_BATCH):
        chunk = b",".join(ids[i:i + HDR_BATCH]).decode()
        typ, data = M.uid("FETCH", chunk, HDR_FIELDS)
        for part in data:
            if not isinstance(part, tuple):
                continue
            uid = _uid(part[0])
            if uid is None:
                continue
            msg = email.message_from_bytes(part[1])
            name, addr = parseaddr(msg.get("From", ""))
            rows.append({"uid": uid, "from": addr.lower(), "name": dec(name),
                         "subject": dec(msg.get("Subject", "")),
                         "bulk": bool(msg.get("List-Unsubscribe") or msg.get("List-ID"))})
    return rows


def classify_rows(rows):
    for r in rows:
        r["bucket"], r["conf"], r["reason"] = mailclass.classify(
            r["from"], r["subject"], r["name"], r["bulk"])
    return rows


def audit(account, rows):
    """Append a reviewable record of everything a sweep trashed."""
    stamp = datetime.now(timezone.utc).isoformat()
    with open(AUDIT_FILE, "a") as f:
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
        M = accounts.connect(acct)
        rows = classify_rows(fetch_headers(M))
        try:
            M.logout()
        except Exception:
            pass
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
        return res
    by = defaultdict(list)
    for r in rows:
        by[r["bucket"]].append(r)
    res["in_inbox"] = len(rows)
    res["by_bucket"] = {b: len(rs) for b, rs in by.items()}
    res["junk"] = sum(len(by[b]) for b in mailclass.JUNK_BUCKETS)
    res["keep"] = len(rows) - res["junk"]
    res["top_senders"] = {b: [s for s, _ in Counter(r["from"] for r in rs).most_common(4)]
                          for b, rs in by.items()}
    res["_rows"] = rows  # internal: used by cmd_plan/sweep, dropped before serialization
    return res


def move_verified(M, uids, dest):
    """Move uids to dest, then re-check INBOX and retry any that didn't leave.
    Returns (moved, stragglers)."""
    M.select('"INBOX"', readonly=False)
    for i in range(0, len(uids), MOVE_BATCH):
        chunk = uids[i:i + MOVE_BATCH]
        try:
            M.uid("MOVE", ",".join(chunk), f'"{dest}"')
        except Exception:
            for u in chunk:  # fall back to per-message so one bad UID can't sink a batch
                try:
                    M.uid("MOVE", u, f'"{dest}"')
                except Exception:
                    pass
        time.sleep(0.2)
    # verification: which requested uids are STILL in INBOX?
    want = set(uids)
    remaining = []
    M.select('"INBOX"', readonly=True)
    typ, d = M.uid("SEARCH", None, "ALL")
    present = set(x.decode() for x in d[0].split())
    remaining = [u for u in uids if u in present and u in want]
    if remaining:  # one retry pass
        M.select('"INBOX"', readonly=False)
        for u in remaining:
            try:
                M.uid("MOVE", u, f'"{dest}"')
            except Exception:
                pass
        M.select('"INBOX"', readonly=True)
        typ, d = M.uid("SEARCH", None, "ALL")
        present = set(x.decode() for x in d[0].split())
        remaining = [u for u in remaining if u in present]
    return len(uids) - len(remaining), remaining


def _count(M, folder):
    typ, d = M.select(f'"{folder}"', readonly=True)
    return int(d[0].decode()) if typ == "OK" else 0


def cmd_status(accts):
    for a in accts:
        try:
            M = accounts.connect(a)
            inbox = _count(M, "INBOX")
            trash = _count(M, accounts.trash_folder(a))
            print(f"{a['email']:30} INBOX {inbox:>6}   Trash {trash:>6}")
            M.logout()
        except Exception as e:
            print(f"{a['email']:30} ERROR: {type(e).__name__}: {e}")


def cmd_inventory(accts):
    """List every folder with message/unseen counts, for any account type."""
    for a in accts:
        try:
            M = accounts.connect(a)
        except Exception as e:
            print(f"\n=== {a['email']} ===\n   ERROR: {type(e).__name__}: {e}")
            continue
        print(f"\n=== {a['email']} ({a['kind']}) ===")
        typ, data = M.list()
        for line in data or []:
            if not line:
                continue
            s = line.decode("utf-8", "replace")
            # LIST: (flags) "delim"|NIL  name   — name may be quoted OR a bare atom
            m = re.match(r'^\([^)]*\)\s+(?:"[^"]*"|NIL)\s+(.+)$', s)
            name = m.group(1).strip().strip('"') if m else None
            if not name:
                continue
            try:
                t, d = M.status(f'"{name}"', "(MESSAGES UNSEEN)")
                s = d[0].decode()
                msgs = (re.search(r"MESSAGES (\d+)", s) or [None, "?"])[1]
                uns = (re.search(r"UNSEEN (\d+)", s) or [None, "?"])[1]
                print(f"   {name:<26} {msgs:>7}  unseen {uns}")
            except Exception:
                print(f"   {name:<26} {'(not selectable)':>7}")
        try:
            M.logout()
        except Exception:
            pass


def sweep_account(acct, dry=False):
    """Dry-run plan or verified move of an account's junk buckets to Trash.
    Prunes matching local DB rows on a real run. Returns a result dict."""
    p = plan_account(acct)
    if p["error"]:
        return {"account": acct["email"], "error": p["error"], "planned": 0}
    junk_rows = [r for r in p["_rows"] if r["bucket"] in mailclass.JUNK_BUCKETS]
    out = {"account": acct["email"], "planned": len(junk_rows), "by_bucket": p["by_bucket"],
           "senders": len(set(r["from"] for r in junk_rows)), "moved": 0,
           "stragglers": [], "error": None}
    if dry or not junk_rows:
        return out
    audit(acct["email"], junk_rows)  # log BEFORE moving so a crash still records intent
    try:
        M = accounts.connect(acct)
        moved, stragglers = move_verified(M, [r["uid"] for r in junk_rows],
                                          accounts.trash_folder(acct))
        try:
            M.logout()
        except Exception:
            pass
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    out["moved"], out["stragglers"] = moved, stragglers
    moved_uids = [r["uid"] for r in junk_rows if r["uid"] not in set(stragglers)]
    import store
    c = store.conn()
    store.delete_by_uids(c, acct["email"], "INBOX", moved_uids)  # don't re-ghost swept mail
    c.commit()
    c.close()
    return out


def cmd_plan(accts, do_sweep=False, dry=False):
    grand = 0
    for a in accts:
        p = plan_account(a)
        if p["error"]:
            print(f"{a['email']:30} ERROR: {p['error']}")
            continue
        print(f"\n=== {a['email']}  ({p['in_inbox']} in INBOX) ===")
        for k in [mailclass.SPAM, mailclass.BLOCKED, mailclass.MKT, mailclass.NOTIF,
                  mailclass.RECEIPT, mailclass.PROTECTED, mailclass.PERSONAL]:
            n = p["by_bucket"].get(k, 0)
            if n:
                tag = " TRASH" if k in mailclass.JUNK_BUCKETS else " keep"
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
        if a["kind"] == "proton":
            print(f"{a['email']:30} SKIPPED — empty Proton Trash with the native button "
                  "(Bridge crashes on bulk expunge).")
            continue
        try:
            M = accounts.connect(a)
            M.select('"[Gmail]/Trash"', readonly=False)
            typ, d = M.uid("SEARCH", None, "ALL")
            uids = d[0].split()
            if not uids:
                print(f"{a['email']:30} Trash already empty")
                M.logout()
                continue
            for i in range(0, len(uids), 500):
                M.uid("STORE", b",".join(uids[i:i + 500]).decode(), "+FLAGS", "(\\Deleted)")
            try:
                M.expunge()
            except Exception:
                pass  # Gmail sometimes drops the connection mid-expunge but completes it
            print(f"{a['email']:30} purged {len(uids)} from Trash")
            try:
                M.logout()
            except Exception:
                pass
        except Exception as e:
            print(f"{a['email']:30} ERROR: {type(e).__name__}: {e}")


def resolve(target):
    if target in (None, "all"):
        return accounts.accounts()
    return [accounts.get(target)]


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    # rule edits need no IMAP connection
    if cmd in ("keep", "block") and len(sys.argv) > 2:
        for tok in sys.argv[2:]:
            t, where = rules.add(tok, cmd)
            print(f"  {t} -> {where}")
        return
    if cmd == "rules":
        r = rules.load()
        for k in rules.KEYS:
            print(f"{k} ({len(r[k])}): {', '.join(r[k]) or '—'}")
        return
    target = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("-") else "all"
    dry = "--dry" in sys.argv
    accts = resolve(target)
    accounts.preload_creds()  # one Touch ID up front for all Gmail accounts
    if cmd == "status":
        cmd_status(accts)
    elif cmd == "inventory":
        cmd_inventory(accts)
    elif cmd == "plan":
        cmd_plan(accts, do_sweep=False)
    elif cmd == "sweep":
        cmd_plan(accts, do_sweep=True, dry=dry)
    elif cmd == "empty-trash":
        cmd_empty_trash(accts)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
