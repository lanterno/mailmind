"""MailMind local web server (stdlib only). Serves the dashboard + a JSON API:
the AI briefing (/api/digest), live per-account/per-bucket curation stats
(/api/stats), and the curation engine itself — reconcile (prune ghost rows),
sweep (verified junk move), learned rules (keep/block), trash, and an audit feed.
A background loop keeps the briefing live.

Usage: python3 server.py [port]   then open http://127.0.0.1:<port>
"""
import sys
import os
import json
import time
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
import store
import accounts
import ingest
import summarize
import mailops
import mailclass
import manage
import rules

HERE = os.path.dirname(__file__)
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8721
SYNC_INTERVAL = 300                       # seconds between automatic syncs
STATS_TTL = 90                            # seconds to cache the live per-account stats
RECON_AUDIT = os.path.join(HERE, "reconcile_audit.jsonl")
SWEEP_AUDIT = os.path.join(HERE, "sweep_audit.jsonl")

_sync = {"running": False, "last": None, "result": "idle"}
_status = {}                              # email -> {"health": ok|syncing|error, "msg": str}
_sync_lock = threading.Lock()
_ops_lock = threading.Lock()              # serialize live-IMAP ops (stats/sweep/reconcile) vs each other
_stats_cache = {"data": None, "exp": 0}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------------------- sync
def sync_once():
    with _sync_lock:
        if _sync["running"]:
            return
        _sync["running"] = True
    try:
        new = 0
        for a in accounts.accounts():
            _status[a["email"]] = {"health": "syncing", "msg": ""}
            try:
                kept, junk, _ = ingest.sync_new(a)
                new += kept
                _status[a["email"]] = {"health": "ok", "msg": f"+{kept}"}
            except Exception as e:
                _status[a["email"]] = {"health": "error", "msg": f"{type(e).__name__}"}
                print(f"[sync] {a['email']}: {type(e).__name__}: {e}", flush=True)
        done = summarize.summarize_pending(limit=300, workers=4)
        _sync["result"] = f"{new} new · {done} summarized"
        _sync["last"] = now_iso()
        print(f"[sync] {_sync['result']}", flush=True)
    finally:
        _sync["running"] = False


def sync_loop():
    while True:
        time.sleep(SYNC_INTERVAL)
        try:
            sync_once()
        except Exception as e:
            print(f"[sync] loop error: {e}", flush=True)


# ----------------------------------------------------------------------------- helpers
def deep_link(row):
    mid = (row["message_id"] or "").strip().strip("<>")
    if row["kind"] == "gmail" and mid:
        return f"https://mail.google.com/mail/u/?authuser={quote(row['account'])}#search/rfc822msgid:{quote(mid)}"
    if row["kind"] == "proton":
        return f"https://mail.proton.me/u/0/almost-all-mail#keyword={quote((row['subject'] or '')[:64])}"
    return ""


def rel_time(iso):
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        if delta.days == 0:
            h = delta.seconds // 3600
            return "just now" if h == 0 else f"{h}h ago"
        if delta.days == 1:
            return "yesterday"
        if delta.days < 7:
            return f"{delta.days}d ago"
        if delta.days < 30:
            return f"{delta.days // 7}w ago"
        return dt.strftime("%b %d")
    except Exception:
        return iso[:10]


def _row_keys(r):
    return r.keys() if hasattr(r, "keys") else {}


def digest_json(qs):
    min_imp = int(qs.get("min", ["1"])[0])
    account = qs.get("account", [None])[0] or None
    c = store.conn()
    rows = store.digest(c, min_importance=min_imp, account=account, limit=400)
    keys = set(rows[0].keys()) if rows else set()
    items = [{
        "pk": r["pk"], "account": r["account"], "kind": r["kind"],
        "from_name": r["from_name"] or r["from_addr"], "from_addr": r["from_addr"],
        "subject": r["subject"] or "(no subject)", "when": rel_time(r["date_iso"]),
        "summary": r["summary"], "importance": r["importance"],
        "category": r["category"], "action": r["action"] or "", "link": deep_link(r),
        "bucket": (r["bucket"] if "bucket" in keys else None),
        "confidence": (r["confidence"] if "confidence" in keys else None),
        "reason": (r["reason"] if "reason" in keys else None),
        "verified": (bool(r["verified"]) if "verified" in keys else False),
        "last_seen": rel_time(r["last_seen"]) if "last_seen" in keys and r["last_seen"] else None,
    } for r in rows]
    accts = [row[0] for row in c.execute("SELECT DISTINCT account FROM messages ORDER BY account").fetchall()]
    st = store.stats(c)
    c.close()
    return {"items": items, "stats": st, "accounts": accts, "status": _status,
            "sync": _sync, "generated": now_iso()}


# ----------------------------------------------------------------------------- live stats
def _strip(p):
    p = dict(p)
    p.pop("_rows", None)
    return p


def live_stats(account=None, refresh=False):
    if not refresh and account is None and _stats_cache["data"] and _stats_cache["exp"] > time.time():
        return _stats_cache["data"]
    accounts.preload_creds()
    with _ops_lock:
        accts = accounts.accounts() if not account or account == "all" else [accounts.get(account)]
        out = [_strip(manage.plan_account(a)) for a in accts]
    data = {
        "accounts": out,
        "buckets": [{"name": b, "is_junk": b in mailclass.JUNK_BUCKETS} for b in
                    [mailclass.SPAM, mailclass.BLOCKED, mailclass.MKT, mailclass.NOTIF,
                     mailclass.RECEIPT, mailclass.PROTECTED, mailclass.PERSONAL]],
        "totals": {
            "in_inbox": sum(a["in_inbox"] for a in out),
            "junk": sum(a["junk"] for a in out),
            "keep": sum(a["keep"] for a in out),
        },
        "generated": now_iso(),
    }
    if account is None or account == "all":
        _stats_cache["data"], _stats_cache["exp"] = data, time.time() + STATS_TTL
    return data


def invalidate_stats():
    _stats_cache["data"], _stats_cache["exp"] = None, 0


# ----------------------------------------------------------------------------- curation ops
def do_reconcile(account, dry_run):
    accounts.preload_creds()
    with _ops_lock:
        accts = accounts.accounts() if not account or account == "all" else [accounts.get(account)]
        results = [ingest.reconcile_account(a, dry_run=dry_run) for a in accts]
    if not dry_run:
        stamp = now_iso()
        with open(RECON_AUDIT, "a") as f:
            for r in results:
                if r.get("pruned"):
                    f.write(json.dumps({"kind": "reconcile", "ts": stamp,
                                        "account": r["account"], "pruned": r["pruned"],
                                        "live": r["live"]}) + "\n")
        invalidate_stats()
    return {"ok": True, "dry_run": dry_run, "results": results,
            "total_ghosts": sum(r["ghosts"] for r in results),
            "total_pruned": sum(r["pruned"] for r in results), "generated": now_iso()}


def do_sweep(account, dry_run):
    accounts.preload_creds()
    with _ops_lock:
        accts = accounts.accounts() if not account or account == "all" else [accounts.get(account)]
        results = [manage.sweep_account(a, dry=dry_run) for a in accts]
    if not dry_run:
        invalidate_stats()
    return {"ok": True, "dry_run": dry_run, "results": results,
            "total_planned": sum(r.get("planned", 0) for r in results),
            "total_moved": sum(r.get("moved", 0) for r in results), "generated": now_iso()}


def read_audit(kind="all", limit=120):
    events = []
    files = []
    if kind in ("all", "sweep"):
        files.append(SWEEP_AUDIT)
    if kind in ("all", "reconcile"):
        files.append(RECON_AUDIT)
    for path in files:
        if not os.path.exists(path):
            continue
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except Exception:
                    continue  # skip a torn final line
    events.sort(key=lambda e: e.get("ts", ""), reverse=True)
    return {"events": events[:limit], "has_more": len(events) > limit}


# ----------------------------------------------------------------------------- HTTP
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            return json.loads(self.rfile.read(length) or b"{}") if length else {}
        except Exception:
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == "/":
                with open(os.path.join(HERE, "dashboard.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif u.path == "/api/digest":
                self._json(digest_json(q))
            elif u.path == "/api/stats":
                self._json(live_stats(q.get("account", [None])[0], q.get("refresh", ["0"])[0] == "1"))
            elif u.path == "/api/rules":
                self._json({"rules": rules.load()})
            elif u.path == "/api/audit":
                self._json(read_audit(q.get("kind", ["all"])[0], int(q.get("limit", ["120"])[0])))
            else:
                self._send(404, b"not found", "text/plain")
        except Exception as e:
            self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)

    def do_POST(self):
        u = urlparse(self.path)
        b = self._body()
        try:
            if u.path == "/api/trash":
                ok, msg = mailops.trash_message(int(b.get("pk")))
                if ok:
                    invalidate_stats()
                self._json({"ok": ok, "msg": msg})
            elif u.path == "/api/refresh":
                threading.Thread(target=sync_once, daemon=True).start()
                self._json({"ok": True, "msg": "sync started"})
            elif u.path == "/api/reconcile":
                self._json(do_reconcile(b.get("account", "all"), bool(b.get("dry_run", False))))
            elif u.path == "/api/sweep":
                self._json(do_sweep(b.get("account", "all"), bool(b.get("dry_run", False))))
            elif u.path == "/api/rules":
                self._json(self._rules_post(b))
            else:
                self._send(404, b"not found", "text/plain")
        except Exception as e:
            self._json({"ok": False, "msg": f"{type(e).__name__}: {e}",
                        "error": f"{type(e).__name__}: {e}"}, 500)

    def _rules_post(self, b):
        token = (b.get("token") or "").strip().lower()
        verdict = b.get("verdict")
        pk = b.get("pk")
        if not token or ("." not in token and "@" not in token):
            return {"ok": False, "msg": "invalid token (need a domain or an address)"}
        if verdict == "remove":
            rules.remove(token)
            placed = None
        elif verdict in ("keep", "block"):
            _, placed = rules.add(token, verdict)
        else:
            return {"ok": False, "msg": "verdict must be keep|block|remove"}
        trashed = False
        if verdict == "block" and pk is not None:  # close the loop: block also trashes now
            try:
                trashed, _ = mailops.trash_message(int(pk))
            except Exception:
                trashed = False
        invalidate_stats()
        return {"ok": True, "token": token, "placed_in": placed,
                "trashed": trashed, "rules": rules.load()}


# ----------------------------------------------------------------------------- boot
def startup():
    print("Preloading credentials from 1Password (approve Touch ID if prompted)...", flush=True)
    print(f"  cached {accounts.preload_creds()} Gmail credentials", flush=True)
    sync_once()


if __name__ == "__main__":
    store.init()
    if os.environ.get("MAILMIND_NOSYNC") == "1":
        print("(auto-sync disabled via MAILMIND_NOSYNC — serving read-only)", flush=True)
    else:
        threading.Thread(target=startup, daemon=True).start()
        threading.Thread(target=sync_loop, daemon=True).start()
    print(f"MailMind → http://127.0.0.1:{PORT}  (serving now)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
