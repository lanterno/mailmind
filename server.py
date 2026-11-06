"""MailMind local web server (stdlib only). Serves the dashboard + a JSON API:
the AI briefing (/api/digest), live per-account/per-bucket curation stats
(/api/stats), and the curation engine itself — reconcile (prune ghost rows),
sweep (verified junk move), learned rules (keep/block), trash, and an audit feed.
A background loop keeps the briefing live.

Usage: python3 server.py [port]   then open http://127.0.0.1:<port>
"""
import sys
import os
import re
import dataclasses
import json
import time
import secrets
import threading
import urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(__file__))
from mailmind.bootstrap.mailbox import store
from mailmind.bootstrap import accounts
from mailmind.bootstrap.accounts import config
from mailmind.bootstrap.mailbox import ingest
from mailmind.bootstrap.mailbox import offline
from mailmind.bootstrap import assistant
from mailmind.bootstrap import attention, calendar, compose, curation, discovery
from mailmind.bootstrap.curation import rules
from mailmind.compose.domain import limits
from mailmind.content import mime
from mailmind.bootstrap import legacy, mailbox
from mailmind.kernel import attention as verdict
from mailmind.kernel.when import now_iso, rel_time
from mailmind.accounts.domain import provider
from mailmind.platform import perimeter, profile, settings, sse, webroot
from mailmind.platform.workers import gates, scheduler
from mailmind.bootstrap.assistant import answers
from mailmind.assistant.application.ports import AIOff
from mailmind.assistant.application.ports import OverLimit

curation.attach(sys.modules[__name__])
mailbox.attach(sys.modules[__name__])

HERE = os.path.dirname(__file__)
SYNC_INTERVAL = 300                       # seconds between automatic syncs
ASSESS_INTERVAL = 300                     # seconds between the assess job's rounds, unless kicked
CSRF = secrets.token_urlsafe(32)          # per-process; required on every mutating POST
MAX_POST = 1 << 20                        # 1 MB cap for control routes




# Expiry rules (rules.EXPIRE, e.g. "github.com after 72h"): move INBOX mail past its age limit
# to Trash on an own timer. Same shape as reconcile — one account per hold of the gate, taken
# NON-blocking so a live user op is never stalled. Not started under NOSYNC (no IMAP writes).
def _expire_secs():
    try:
        return int(os.environ.get("MAILMIND_EXPIRE_SECS", "3600"))  # 0 disables
    except ValueError:
        print("[expire] bad MAILMIND_EXPIRE_SECS — using 3600", flush=True)
        return 3600


_EXPIRE_INTERVAL = _expire_secs()


def forget_round():
    """What's kept of a message no copy of has come back to in a month goes, and so do queue rows
    finished a month ago."""
    c = store.conn()
    try:
        store.forget_gone_messages(c, int(time.time()))
        store.read_mut_forget_finished(c, int(time.time()))
        answers.batch_forget_finished(c, int(time.time()))
        c.commit()
    finally:
        c.close()


def nosync():
    """Whether this process runs read-only (mailmind.platform.profile: MAILMIND_PROFILE, or the
    older MAILMIND_NOSYNC), with no background sync, reconcile or expiry."""
    return profile.current() is not profile.Profile.FULL


def _kick(job):
    """Run `job`'s next round now, if this process schedules it (read-only schedules no assess)."""
    if job in JOBS.names():
        JOBS.kick(job)


# ----------------------------------------------------------------------------- helpers
def deep_link(row, wmap=None):
    """The message in its provider's web client (ProviderProfile.deep_link), else the account's
    webmail (e.g. Nextcloud Mail), else ""."""
    return provider.resolve({"kind": row["kind"]}).deep_link(
        account=row["account"], message_id=row["message_id"], subject=row["subject"],
        webmail=(wmap or {}).get(row["account"]))


def _attention(r):
    """What the verdict on row `r` asks of you. The page reads these and never works them out."""
    return verdict.answers(r["importance"], r["category"], r["action"])


def digest_json(qs):
    min_imp = int(qs.get("min", ["1"])[0])
    account = qs.get("account", [None])[0] or None
    c = store.conn()
    rows = store.digest(c, min_importance=min_imp, account=account, limit=400)
    keys = set(rows[0].keys()) if rows else set()
    amap = config.alias_map()
    wmap = config.webmail_map()
    items = [{
        "pk": r["pk"], "key": r["message_key"], "account": r["account"], "kind": r["kind"],
        "alias": amap.get(r["account"]),
        "from_name": r["from_name"] or r["from_addr"], "from_addr": r["from_addr"],
        "subject": r["subject"] or "(no subject)", "when": rel_time(r["date_iso"]),
        "summary": r["summary"], "importance": r["importance"], **_attention(r),
        "category": r["category"], "action": r["action"] or "", "link": deep_link(r, wmap),
        "bucket": (r["bucket"] if "bucket" in keys else None),
        "confidence": (r["confidence"] if "confidence" in keys else None),
        "reason": (r["reason"] if "reason" in keys else None),
        "verified": (bool(r["verified"]) if "verified" in keys else False),
        "last_seen": rel_time(r["last_seen"]) if "last_seen" in keys and r["last_seen"] else None,
        # what a Mailbox row needs to draw a digest item like a folder item (Needs you, briefing topics)
        "seen": bool(r["seen"]), "flagged": bool(r["flagged"]), "has_attach": bool(r["has_attach"]),
        "thread_id": r["thread_id"], "folder": r["folder"], "ts": r["ts"],
    } for r in rows]
    accts = [row[0] for row in c.execute("SELECT DISTINCT account FROM messages ORDER BY account").fetchall()]
    st = store.stats(c)
    c.close()
    # send_accounts = the authoritative configured (enabled) identities — the compose
    # From list + reply-all self-dedupe set, independent of what's been synced yet.
    send_accounts = [a["email"] for a in accounts.accounts()]
    return {"items": items, "stats": st, "accounts": accts, "send_accounts": send_accounts,
            "aliases": amap, "signatures": config.signature_map(),
            "signature_default": config.signature_default(),
            "status": mailbox.syncing._status, "sync": mailbox.syncing._sync, "ai": assistant.calls.status(), "profile": profile.current().value,
            "generated": now_iso()}


def _safe_filename(name):
    """Never trust an email-supplied filename: strip path + control/quote/CRLF chars."""
    name = (name or "attachment")
    name = name.split("/")[-1].split("\\")[-1]
    name = re.sub(r'[\x00-\x1f\x7f"\r\n]', "_", name).strip(" .") or "attachment"
    return name[:255]


def post_body_cap(path):
    """The most a POST to `path` may carry, in bytes, checked against its Content-Length before
    the body is read: 38 MB where mail and its attachments go up, 1 MB everywhere else."""
    return limits.MAX_SEND_POST if path in ("/api/send", "/api/draft") else MAX_POST


_UI_QUIET = {"/api/events", "/api/folders", "/api/outbox", "/api/pending", "/api/stats",
             "/api/ai/draft-ahead"}   # fetched by hidden tabs via SSE — incl. the draft-ready echo itself,
                                      # or the tick's own events would hold the vacation gate open forever


def _ui_touch(path):
    """Someone is here, for the cost governor's presence gate on drafting ahead. _UI_QUIET routes
    are excluded because the SSE followup/outbox listeners re-fetch them even from a hidden,
    forgotten-open tab."""
    if path.startswith("/api/") and path not in _UI_QUIET:
        assistant.governor().saw_someone()


AI_OVER_HEADER = "X-MailMind-AI-Over-Limit"   # "allow": the user said this request may go over


def _over_limit_answer(e):
    """The 402 an interactive AI request gets at the daily limit. The page asks the user, then
    sends the request again with AI_OVER_HEADER."""
    return {"ok": False, "error": "today's AI spending has reached the daily limit", "over_limit": True,
            "spent_micros": e.spent_micros, "limit_micros": e.limit_micros}, 402


def _ai_off_answer():
    """The 409 an AI request gets with AI off. Nothing was sent, and asking again won't help."""
    return {"ok": False, "error": "AI is off: MailMind has no Claude API key", "ai_off": True}, 409


# ----------------------------------------------------------------------- accounts
def account_test(body):
    """Test a SAVED account ({email}) or an UNSAVED draft (no disk write).
    Errors are sanitized — never echoes the attempted password or server banner.

    A saved account's credential goes only to its saved server. A draft may only try a password
    typed with it, against the host typed with it: a draft naming a stored secret (an op
    reference, an environment variable, the Bridge password) would send that secret to whatever
    host the request names, so it is refused until the account is saved."""
    t0 = time.time()
    try:
        if body.get("draft"):
            d = body["draft"]
            ty = d.get("type", "imap")
            td = config.TYPE_DEFAULTS.get(ty, config.TYPE_DEFAULTS["imap"])
            cs = (d.get("credential") or {})
            acct = {"email": d.get("email"), "kind": ty,
                    "host": d.get("host") or td["host"], "port": int(d.get("port") or td["port"]),
                    "starttls": bool(d.get("starttls", td["starttls"])),
                    "strip_spaces": bool(d.get("strip_spaces", td["strip_spaces"])),
                    "trash": td["trash"], "credential": {"plain": cs.get("value")}}
            if not acct["host"]:
                return {"ok": False, "error": "a host is required"}
            if cs.get("style") != "plain" or not cs.get("value"):
                return {"ok": False, "error": "save the account to test a stored secret; "
                                              "before then only a typed password is tried"}
        else:
            acct = accounts.get(body["email"])
        # stateless probe — shares no IMAP state, selects no folder, uses no pooled conn, so it needs no
        # IMAP gate (holding it here froze all mail ops for up to 60s on a mistyped/black-holed host);
        # a short timeout also gives the user fast feedback on a bad host.
        s = mailbox.open_session(acct, timeout=15)
        try:
            s.logout()
        except Exception:
            pass
        return {"ok": True, "connect": True, "email": acct["email"],
                "detail": "login ok", "ms": int((time.time() - t0) * 1000)}
    except KeyError:
        return {"ok": False, "error": "account not found"}
    except Exception as e:
        return {"ok": True, "connect": False,
                "email": body.get("email") or (body.get("draft") or {}).get("email"),
                "error": accounts.quiet_error(e)}


def account_trust_cert(body):
    """Trust the account's servers again after its loopback certificate changed (Bridge reinstalled
    or reset): forget its pins, and sync it, which pins the certificate it meets now."""
    email = (body.get("email") or "").strip()
    if not config.forget_tls_pins(email):
        return {"ok": False, "error": "not found"}, 404
    if email in {a["email"] for a in accounts.accounts()}:    # a disabled one waits until enabled
        return mailbox.syncing.do_sync_retry({"account": email})
    return {"ok": True, "account": email}, 200


def account_mutate(action, body):
    if action == "add":
        return {"ok": True, "account": config.add_account(body)}
    if action == "edit":
        row = config.update_account(body.get("email"), body)
        return {"ok": True, "account": row} if row else {"ok": False, "error": "not found"}
    if action == "delete":
        em = body.get("email")
        if config.remove_account(em):
            mailbox.syncing._status.pop(em, None)
            return {"ok": True, "removed": em}
        return {"ok": False, "error": "not found"}
    return {"ok": False, "error": "unknown action"}


# ----------------------------------------------------------------------------- HTTP
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_response_only(self, code, message=None):
        self._status, self._named = code, set()
        super().send_response_only(code, message)

    def send_header(self, keyword, value):
        self._named.add(keyword.lower())
        super().send_header(keyword, value)

    def end_headers(self):
        """Every answer, an error http.server writes itself included, carries the perimeter's
        headers; the ones a route set itself stand."""
        if self._status >= 200:
            for k, v in perimeter.answer_headers(urllib.parse.urlsplit(getattr(self, "path", "")).path):
                if k.lower() not in self._named:
                    self.send_header(k, v)
        super().end_headers()

    def _send(self, code, body, ctype, headers=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _send_raw(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # belt-and-braces: even if a browser ignores Content-Disposition, sandbox neuters it
        self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _serve_events(self):
        """The live event stream (mailmind.platform.sse): blocks this handler thread, passing on
        each event until the tab hangs up. A tab that reconnects resumes after the Last-Event-ID
        its EventSource sends, or gets one resync. A keepalive on a quiet stream finds a dead
        socket."""
        tab = sse.HUB.listen(self.headers.get("Last-Event-ID"))
        if tab is None:
            return self._json({"ok": False, "error": "too many listeners"}, 503)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(tab.opening() + sse.hello(webroot.build()))
            self.wfile.flush()
            while True:
                events = tab.next(sse.HEARTBEAT)
                self.wfile.write(b"".join(e.frame() for e in events) or b": keepalive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            pass
        finally:
            tab.close()

    def _serve_asset(self, path):
        """One of the page's files, by ETag, compressed for a browser that takes it."""
        found = webroot.asset(path)
        if found is None:
            return self._send(404, b"not found", "text/plain")
        headers = [("ETag", found.etag), ("Cache-Control", found.cache), ("Vary", "Accept-Encoding")]
        if self.headers.get("If-None-Match") == found.etag:
            self.send_response(304)
            for k, v in headers:
                self.send_header(k, v)
            return self.end_headers()
        body = found.body
        if found.gzipped and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            body = found.gzipped
            headers.append(("Content-Encoding", "gzip"))
        self._send(200, body, found.ctype, headers)

    def _serve_attachment(self, pk, part_id):
        if not re.match(r"^[0-9]+$", part_id or ""):     # only the part ids mime gives
            return self._json({"ok": False, "error": "bad part id"}, 400)
        c = store.conn(); row = store.get_message(c, pk); c.close()
        if not row:
            return self._json({"ok": False, "error": "not found"}, 404)
        gates.run(row["account"], lambda: ingest.fetch_body(pk),   # ensure body + attachment metadata exist
                  priority=gates.Priority.INTERACTIVE)
        c = store.conn()
        att = c.execute("SELECT filename,content_type FROM attachments WHERE message_pk=? AND part_id=?",
                        (pk, part_id)).fetchone()
        raw = store.get_body(c, pk)
        c.close()
        if not att or not raw:
            return self._json({"ok": False, "error": "no such part"}, 404)
        data, _ = mime.extract_part(raw, part_id)
        if data is None:
            return self._json({"ok": False, "error": "no such part"}, 404)
        if len(data) > limits.MAX_ATTACH_BYTES:
            return self._json({"ok": False, "error": "too large"}, 413)
        ctype = (att["content_type"] or "application/octet-stream").split(";")[0].strip()
        # never inline-render active types from the top frame; force download regardless
        fn = _safe_filename(att["filename"])
        disp = "attachment; filename*=UTF-8''" + urllib.parse.quote(fn, safe="")
        self._send_raw(200, data, ctype, {"Content-Disposition": disp})

    def _serve_db_export(self):
        """Stream a consistent SQLite snapshot (checkpoint + .backup) as a download, chunked so a
        large DB never buffers in memory. Read-only: cannot touch the live DB. Temp file removed after."""
        import export, tempfile, shutil
        tmp = tempfile.mkdtemp(prefix="mm-export-")
        try:
            path = export.export_db(tmp)
            size = os.path.getsize(path)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition",
                             "attachment; filename=" + os.path.basename(path))
            self.end_headers()
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(262144)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except Exception as e:
            print(f"[export] db error: {type(e).__name__}: {e}", flush=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _body(self):
        """The request's JSON object, {} for no body, or None for a body that isn't a JSON object
        (an array, a string, not JSON at all), which the route answers 400 once the guards pass."""
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            b = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except Exception:
            return None
        return b if isinstance(b, dict) else None

    def do_GET(self):
        no = perimeter.refuse_host(self.headers, settings.current().port)
        if no:
            return self._json(no.answer(), no.status)
        u = urlparse(self.path)
        no = perimeter.refuse_cross_site(self.headers, u.path)
        if no:
            return self._json(no.answer(), no.status)
        q = parse_qs(u.query)
        _ui_touch(u.path)
        over = assistant.allowing_over(self.headers.get(AI_OVER_HEADER) == "allow")
        over.__enter__()
        try:
            if u.path == "/":
                self._send(200, webroot.page(CSRF).encode("utf-8"), "text/html; charset=utf-8",
                           [("Content-Security-Policy-Report-Only", perimeter.page_policy()),
                            ("Cache-Control", "no-store")])     # it holds this run's token
            elif u.path == "/next" or u.path.startswith("/next/"):
                built = webroot.next_page(CSRF)
                if built is None:
                    self._send(404, b"The new app isn't built. Run: just build-web\n", "text/plain; charset=utf-8")
                else:
                    self._send(200, built.encode("utf-8"), "text/html; charset=utf-8",
                               [("Content-Security-Policy-Report-Only", perimeter.page_policy()),
                                ("Cache-Control", "no-store")])
            elif u.path.startswith(webroot.PREFIX):
                self._serve_asset(u.path)
            elif u.path == "/api/digest":
                self._json(digest_json(q))
            elif u.path == "/api/briefing":     # what is stored of the "Today" card; POST writes it
                self._json(*assistant.asking.briefing(q, generate=False))
            elif u.path == "/api/review":       # what is stored of the Weekly Review; POST writes it
                self._json(*assistant.asking.review(q, generate=False))
            elif u.path == "/api/briefing/since":   # what is stored of the "since you last looked" delta
                self._json(*assistant.asking.delta(q, generate=False))
            elif u.path == "/api/ai/draft-ahead":   # serve/peek; the generation KICK needs the CSRF token
                self._json(*assistant.drafting.get(
                    q, kick_ok=perimeter.token_matches(self.headers, CSRF)))
            elif u.path == "/api/stats":
                self._json(mailbox.stats.live(q.get("account", [None])[0], q.get("refresh", ["0"])[0] == "1"))
            elif u.path == "/api/rules":
                self._json({"rules": rules.load()})
            elif u.path == "/api/views":
                self._json({"ok": True, "views": discovery.views.merged()})
            elif u.path == "/api/audit":
                self._json(curation.AUDIT.read(q.get("kind", ["all"])[0], int(q.get("limit", ["120"])[0])))
            elif u.path == "/api/folders":
                _fa = q.get("account", [None])[0]
                if not _fa:
                    self._json({"ok": False, "error": "account required", "folders": []}, 400)
                else:
                    self._json(mailbox.folders.of_account(_fa))
            elif u.path == "/api/messages":
                self._json(mailbox.queries.messages_in(q.get("account", [None])[0], q.get("folder", ["INBOX"])[0],
                                       int(q.get("offset", ["0"])[0]), int(q.get("limit", ["80"])[0]),
                                       group=(q.get("group", [""])[0] == "thread")))
            elif u.path == "/api/upcoming":     # read-only: invites in fetched mail, nothing fetched
                self._json(calendar.agenda.upcoming(q.get("account", [None])[0] or None))
            elif u.path == "/api/thread":
                self._json(mailbox.queries.thread_json(q.get("account", [None])[0], q.get("thread_id", [None])[0]))
            elif u.path == "/api/search":
                self._json(discovery.search.answer(q))
            elif u.path == "/api/outbox":
                self._json(compose.outbox.list_recent(int(q.get("limit", ["50"])[0])))
            elif u.path == "/api/pending":
                self._json(offline.list_open())
            elif u.path == "/api/contacts":
                self._json(discovery.contacts.autocomplete(q.get("q", [""])[0]))
            elif u.path == "/api/contact":
                self._json(*discovery.contacts.card(q.get("addr", [""])[0]))
            elif u.path == "/api/screener":
                self._json(curation.screener.queue(q.get("account", [None])[0]))
            elif u.path == "/api/documents":
                self._json(discovery.documents.page(q))
            elif u.path == "/api/cleanup/senders":
                self._json(curation.cleanup.senders(q))
            elif u.path == "/api/cleanup/sender":
                self._json(curation.cleanup.sender(q))
            elif u.path == "/api/account-health":
                self._json(mailbox.syncing.account_health_json())
            elif u.path == "/api/debug/pool":
                from mailmind.bootstrap.mailbox import pool
                self._json({"ok": True, **pool.stats()})
            elif u.path == "/api/debug/jobs":    # the background jobs' timing and last outcomes
                self._json({"ok": True, "profile": profile.current().value, "jobs": JOBS.snapshot(),
                            "generated": now_iso()})
            elif u.path == "/api/debug/ai":      # what AI cost each local day, by feature
                days = max(1, min(366, int(q.get("days", ["30"])[0])))
                self._json({"ok": True, "days": assistant.calls.usage(days), "governor": assistant.governor().snapshot(),
                            "batch": assistant.triage.batch_status(),
                            "generated": now_iso()})
            elif u.path == "/api/export/plan":
                import export
                self._json(export.plan(q.get("account", [None])[0]))
            elif u.path == "/api/export":
                return self._serve_db_export()
            elif u.path == "/api/events":
                return self._serve_events()
            elif u.path.startswith("/api/message/") and "/attachment/" in u.path:
                parts = u.path.split("/")     # ['', 'api', 'message', '<pk>', 'attachment', '<part_id>']
                self._serve_attachment(int(parts[3]), parts[5])
            elif u.path.startswith("/api/message/") and u.path.endswith("/body"):
                self._json(mailbox.bodies.message_body(int(u.path.split("/")[3]), q.get("images", ["0"])[0] == "1",
                                        prefetch=(q.get("prefetch", ["0"])[0] == "1")))
            elif u.path == "/api/accounts":
                em = q.get("email", [None])[0]
                if em:
                    row = next((r for r in config.masked_rows() if r["email"] == em), None)
                    self._json({"ok": bool(row), "account": row}, 200 if row else 404)
                else:
                    self._json({"ok": True, "accounts": config.masked_rows(),
                                "signature_default": config.signature_default(), "generated": now_iso()})
            else:
                self._send(404, b"not found", "text/plain")
        except OverLimit as e:      # the page asks before it goes over
            self._json(*_over_limit_answer(e))
        except AIOff:               # no key: nothing was sent, and nothing will be until one is set
            self._json(*_ai_off_answer())
        except Exception as e:                # scrub: log the detail, never echo it to the client
            print(f"[get] {u.path}: {type(e).__name__}: {e}", flush=True)
            self._json({"ok": False, "error": "request failed"}, 500)
        finally:
            over.__exit__(None, None, None)

    def do_POST(self):
        u = urlparse(self.path)
        # 1) Cap the body size BEFORE reading it, so an unauthenticated/cross-tab
        #    request can't make us read+parse an arbitrary-size payload.
        no = perimeter.refuse_body(self.headers, post_body_cap(u.path))
        if no:
            self.close_connection = True
            return self._json(no.answer(), no.status)
        b = self._body()                  # now safe to consume the request body
        # 2) Then gate every mutation: host, CSRF token, and on the send routes a matching Origin.
        no = perimeter.refuse_mutation(u.path, self.headers, port=settings.current().port, token=CSRF)
        if no:
            return self._json(no.answer(), no.status)
        if b is None:
            return self._json({"ok": False, "error": "bad request"}, 400)
        _ui_touch(u.path)                 # only a request that got past the guards: someone is here
        over = assistant.allowing_over(self.headers.get(AI_OVER_HEADER) == "allow")
        over.__enter__()
        try:
            if u.path == "/api/send":
                try:
                    res, code = compose.sending.send(b)
                except ValueError as ve:           # our own validation messages are secret-free
                    res, code = {"ok": False, "error": str(ve)}, 400
                except Exception as ex:            # never leak a raw exception on the send route
                    print(f"[send] error: {type(ex).__name__}: {ex}", flush=True)
                    res, code = {"ok": False, "error": "send failed"}, 500
                self._json(res, code)
            elif u.path == "/api/invite/rsvp":
                try:
                    res, code = calendar.rsvp.respond(b)
                except Exception as ex:            # never leak a raw exception on a send route
                    print(f"[rsvp] error: {type(ex).__name__}: {ex}", flush=True)
                    res, code = {"ok": False, "error": "RSVP failed"}, 500
                self._json(res, code)
            elif u.path == "/api/pending/retry":
                self._json(*mailbox.filing.do_pending_retry(int(b.get("id"))))
            elif u.path == "/api/pending/discard":
                self._json(*mailbox.filing.do_pending_discard(int(b.get("id"))))
            elif u.path == "/api/outbox/retry":
                ok = compose.outbox.retry(int(b.get("id")))
                self._json({"ok": ok}, 200 if ok else 404)
            elif u.path == "/api/outbox/cancel":
                self._json({"ok": compose.outbox.cancel(int(b.get("id")))})
            elif u.path == "/api/outbox/sendnow":
                ok = compose.outbox.send_now(int(b.get("id")))
                self._json({"ok": ok}, 200 if ok else 404)
            elif u.path == "/api/trash":
                self._json(mailbox.filing.do_trash(b))
            elif u.path in ("/api/thread/archive", "/api/thread/trash"):
                self._json(mailbox.filing.do_thread_file(u.path, b))
            elif u.path == "/api/undo":
                res, code = mailbox.undo.undo_last()
                self._json(res, code)
            elif u.path == "/api/refresh":
                threading.Thread(target=mailbox.syncing.sync_once, daemon=True).start()
                self._json({"ok": True, "msg": "sync started"})
            elif u.path == "/api/reconcile":
                self._json(mailbox.syncing.do_reconcile(b.get("account", "all"), bool(b.get("dry_run", False)),
                                        force=b.get("force") is True))
            elif u.path == "/api/sweep":
                self._json(curation.sweeps.run(b.get("account", "all"), bool(b.get("dry_run", False))))
            elif u.path == "/api/rules":
                self._json(curation.screener.rule(b))
            elif u.path == "/api/cleanup/execute":
                try:
                    res, code = curation.cleanup.execute(b)
                except Exception as ex:
                    print(f"[cleanup] error: {type(ex).__name__}: {ex}", flush=True)
                    res, code = {"ok": False, "error": "cleanup failed"}, 500
                self._json(res, code)
            elif u.path == "/api/views":
                self._json(discovery.views.post(b))
            elif u.path in ("/api/flag", "/api/seen", "/api/archive", "/api/move", "/api/junk"):
                self._json(mailbox.filing.do_write(u.path, b))
            elif u.path == "/api/draft":
                try:
                    res, code = compose.drafts.save(b)
                except ValueError as ve:
                    res, code = {"ok": False, "error": str(ve)}, 400
                self._json(res, code)
            elif u.path == "/api/snooze":
                self._json(*attention.snoozing.snooze(b))
            elif u.path == "/api/unsnooze":
                self._json(*attention.snoozing.unsnooze(b))
            elif u.path == "/api/mute":
                self._json(*attention.muting.mute(b))
            elif u.path == "/api/unmute":
                self._json(*attention.muting.unmute(b))
            elif u.path == "/api/followups/dismiss":
                self._json(*attention.followups.dismiss(b))
            elif u.path == "/api/replylater":
                self._json(*attention.followups.reply_later(b))
            elif u.path == "/api/sync-retry-account":
                self._json(*mailbox.syncing.do_sync_retry(b))
            elif u.path == "/api/ai/summarize":
                try:
                    res, code = assistant.asking.summarize(b)
                except (OverLimit, AIOff):   # the 402 (the page asks) or the 409
                    raise
                except Exception as ex:
                    print(f"[ai] error: {type(ex).__name__}: {ex}", flush=True)
                    res, code = {"ok": False, "error": "summary failed"}, 500
                self._json(res, code)
            elif u.path == "/api/ai/draft-reply":
                try:
                    res, code = assistant.drafting.reply(b)
                except (OverLimit, AIOff):   # the 402 (the page asks) or the 409
                    raise
                except Exception as ex:
                    print(f"[ai] draft error: {type(ex).__name__}: {ex}", flush=True)
                    res, code = {"ok": False, "error": "draft failed"}, 500
                self._json(res, code)
            elif u.path == "/api/ai/rewrite":
                try:
                    res, code = assistant.asking.rewrite(b)
                except (OverLimit, AIOff):   # the 402 (the page asks) or the 409
                    raise
                except Exception as ex:
                    print(f"[ai] rewrite error: {type(ex).__name__}: {ex}", flush=True)
                    res, code = {"ok": False, "error": "rewrite failed"}, 500
                self._json(res, code)
            elif u.path == "/api/ai/ask":
                try:
                    res, code = assistant.asking.ask(b)
                except (OverLimit, AIOff):   # the 402 (the page asks) or the 409
                    raise
                except Exception as ex:
                    print(f"[ai] ask error: {type(ex).__name__}: {ex}", flush=True)
                    res, code = {"ok": False, "error": "ask failed"}, 500
                self._json(res, code)
            elif u.path == "/api/ai/do-confirm":   # carry out the plan a preview froze
                self._json(*assistant.bulk.confirm(b))
            elif u.path == "/api/ai/do-plan":     # read-only: NL command → validated, capped action PREVIEW
                try:
                    res, code = assistant.bulk.plan(b)
                except (OverLimit, AIOff):   # the 402 (the page asks) or the 409
                    raise
                except Exception as ex:
                    print(f"[ai] do-plan error: {type(ex).__name__}: {ex}", flush=True)
                    res, code = {"ok": False, "error": "plan failed"}, 500
                self._json(res, code)
            elif u.path == "/api/draft/resume":      # read-only: a saved draft, back into the composer
                try:
                    res, code = compose.drafts.resume(b if isinstance(b, dict) else {})
                except Exception as ex:
                    print(f"[resume] error: {type(ex).__name__}", flush=True)
                    res, code = {"ok": False, "error": "Couldn’t open this draft."}, 500
                self._json(res, code)
            elif u.path == "/api/contacts/known":   # read-only: which recipients you've exchanged mail with
                try:
                    res, code = discovery.known.ask(b.get("addrs") if isinstance(b, dict) else None)
                except Exception as ex:             # class only: the payload is a list of addresses
                    print(f"[contacts] known error: {type(ex).__name__}", flush=True)
                    res, code = {"ok": False, "error": "lookup failed"}, 500
                self._json(res, code)
            elif u.path == "/api/briefing":   # write the "Today" card once, if none is stored
                try:
                    res, code = assistant.asking.briefing(b if isinstance(b, dict) else {})
                except (OverLimit, AIOff):   # the 402 (the page asks) or the 409
                    raise
                except Exception as ex:
                    print(f"[ai] briefing error: {type(ex).__name__}: {ex}", flush=True)
                    res, code = {"ok": False, "error": "briefing failed"}, 500
                self._json(res, code)
            elif u.path == "/api/review":   # write the Weekly Review once, if none is stored
                try:
                    res, code = assistant.asking.review(b if isinstance(b, dict) else {})
                except (OverLimit, AIOff):   # the 402 (the page asks) or the 409
                    raise
                except Exception as ex:
                    print(f"[ai] review error: {type(ex).__name__}: {ex}", flush=True)
                    res, code = {"ok": False, "error": "review failed"}, 500
                self._json(res, code)
            elif u.path == "/api/briefing/since":   # write the "since you last looked" delta once
                try:
                    res, code = assistant.asking.delta(b if isinstance(b, dict) else {})
                except (OverLimit, AIOff):   # the 402 (the page asks) or the 409
                    raise
                except Exception as ex:
                    print(f"[ai] delta error: {type(ex).__name__}: {ex}", flush=True)
                    res, code = {"ok": False, "error": "delta failed"}, 500
                self._json(res, code)
            elif u.path == "/api/review/refresh":     # force re-run the Weekly Review (behind _mutation_guard)
                try:
                    res, code = assistant.asking.review(b, force=True)
                except (OverLimit, AIOff):   # the 402 (the page asks) or the 409
                    raise
                except Exception as ex:
                    print(f"[ai] review error: {type(ex).__name__}: {ex}", flush=True)
                    res, code = {"ok": False, "error": "review failed"}, 500
                self._json(res, code)
            elif u.path == "/api/briefing/refresh":   # force re-run (behind _mutation_guard)
                try:
                    res, code = assistant.asking.briefing(b, force=True)
                except (OverLimit, AIOff):   # the 402 (the page asks) or the 409
                    raise
                except Exception as ex:
                    print(f"[ai] briefing error: {type(ex).__name__}: {ex}", flush=True)
                    res, code = {"ok": False, "error": "briefing failed"}, 500
                self._json(res, code)
            elif u.path.startswith("/api/accounts/"):
                action = u.path.rsplit("/", 1)[-1]
                if action == "test":
                    self._json(account_test(b))
                elif action == "trust-cert":
                    self._json(*account_trust_cert(b))
                elif action == "signature-default":
                    config.set_signature_default(b.get("signature") or "")
                    self._json({"ok": True, "signature_default": config.signature_default()})
                elif action in ("add", "edit", "delete"):
                    try:
                        self._json(account_mutate(action, b))
                    except ValueError as ve:
                        self._json({"ok": False, "error": str(ve)}, 400)
                    finally:
                        mailbox.stats.invalidate()
                else:
                    self._send(404, b"not found", "text/plain")
            else:
                self._send(404, b"not found", "text/plain")
        except (TypeError, ValueError):       # malformed request input (bad/missing int id/pk) → clean 400
            self._json({"ok": False, "error": "bad request"}, 400)
        except profile.ReadOnlyError as e:    # a write this process may not make (and nothing queued it)
            print(f"[post] {self.path}: read-only, refused {e}", flush=True)
            self._json({"ok": False, "error": "MailMind is running read-only", "read_only": True}, 409)
        except OverLimit as e:      # the page asks before it goes over
            self._json(*_over_limit_answer(e))
        except AIOff:               # no key: nothing was sent, and nothing will be until one is set
            self._json(*_ai_off_answer())
        except Exception as e:                # scrub: log the detail, never echo it to the client
            print(f"[post] {self.path}: {type(e).__name__}: {e}", flush=True)
            self._json({"ok": False, "error": "request failed"}, 500)
        finally:
            over.__exit__(None, None, None)


# ----------------------------------------------------------------------------- boot
JOBS = scheduler.Scheduler()             # the background jobs; legacy.start puts the running ones here


def schedule(jobs):
    """Put the background work on `jobs`, a Scheduler. Snooze wake always runs, since it needs no
    mail server. Read-only, nothing else does: no sync, triage, backfill, pre-drafting, reconcile
    or expiry."""
    jobs.add("snooze", attention.clocked.round, every=60, first=60)
    if nosync():
        return
    jobs.add("sync", mailbox.syncing.sync_rounds(), every=SYNC_INTERVAL, first=0)
    jobs.add("assess", assistant.triage.round, every=ASSESS_INTERVAL, first=20)         # triage; a sync with new mail kicks it
    jobs.add("backfill", mailbox.syncing.backfill_round, every=600, first=45)              # old mail, full-text searchable
    if not os.environ.get("MAILMIND_NO_DRAFT_TICK"):
        jobs.add("draft-ahead", assistant.drafting.rounds(), every=assistant.drafting.DRAFT_TICK_INTERVAL,
                 first=assistant.drafting.DRAFT_TICK_STARTUP_DELAY)                           # top-K reply pre-gen (AI-polite)
    if mailbox.syncing._RECONCILE_INTERVAL > 0:                                            # ghost prune, one account a round
        jobs.add("reconcile", mailbox.syncing.reconcile_rounds(), every=max(30, mailbox.syncing._RECONCILE_INTERVAL),
                 first=min(120, mailbox.syncing._RECONCILE_INTERVAL))
    if _EXPIRE_INTERVAL > 0:                                               # the expiry rules
        jobs.add("expire", curation.expiry.round, every=_EXPIRE_INTERVAL, first=min(180, _EXPIRE_INTERVAL))
    jobs.add("forget", forget_round, every=86400, first=600)               # keys with no copy left


def _after_send(_sent):
    """Compose's Sent or Failed: a queued send went out, or an attempt at one failed (with
    SSE live the page doesn't poll the outbox, so a send given up on would still look pending)."""
    mailbox.stats.invalidate()
    sse.publish("outbox", {})       # nudge the client to refresh the outbox view instantly


def _after_flags_changed(changed):
    """FlagsChanged: mail read or starred elsewhere shows on a list that's open."""
    sse.publish("flags-changed", {"account": changed.account, "folder": changed.folder, "count": changed.count})


def _after_draft_removed(gone):
    """Compose's DraftCopyRemoved: a send's draft left the server."""
    mailbox.folders.list_changed(gone.account, gone.folder)


def _after_idle(changed):
    """InboxChanged: IDLE saw these accounts' INBOX change."""
    mailbox.syncing.sync_guarded(set(changed.accounts))


class _Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # A browser dropping a socket (reload, closed tab, unused preconnect) isn't a fault worth
        # a traceback; anything else still prints one.
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        settings.configure(dataclasses.replace(settings.current(), port=int(sys.argv[1])))
    legacy.start(sys.modules[__name__])  # the schema, the workers' wiring, and the workers
    port = settings.current().port
    print(f"MailMind → http://127.0.0.1:{port}  (serving now)", flush=True)
    _Server(("127.0.0.1", port), H).serve_forever()
