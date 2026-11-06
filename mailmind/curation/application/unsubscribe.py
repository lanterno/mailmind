"""Leaving a sender's list: finding the way, and trying it once. Terminal outcomes are latched by the sender's
key, so a repeat never fires again, and a failure stays retryable."""
import email.message
import email.policy
import email.utils
import json
import re
import threading
import time
from datetime import datetime, timezone

from mailmind.content import mime
from mailmind.curation.application.ports import ListHeaders, SafeHttp, SenderMail, SystemSend
from mailmind.curation.domain.unsubscribe import NO_METHOD, TERMINAL, parse_list_unsub
from mailmind.platform import profile

MIN_GAP = 3.0              # >= 3s between ANY two outbound unsubscribes (politeness)
_rate = {"last": 0.0, "lock": threading.Lock()}


def rate_gate():
    """Process-global politeness: >= MIN_GAP between any two outbound unsubscribes."""
    with _rate["lock"]:
        wait = MIN_GAP - (time.time() - _rate["last"])
        if wait > 0:
            time.sleep(min(wait, MIN_GAP))
        _rate["last"] = time.time()


class Unsubscribing:
    def __init__(self, mail: SenderMail, headers: ListHeaders, http: SafeHttp, send: SystemSend):
        self._mail, self._headers, self._http, self._send = mail, headers, http, send

    def _from_server(self, row):
        """Ask the server for one message's unsubscribe headers alone: mail from before ingest kept them,
        with no body stored. Caches what it hears; None if it can't say now."""
        raw = self._headers.read(row)
        if raw is None:
            return None
        parsed = parse_list_unsub(raw)
        self._mail.remember_unsub(row["pk"], parsed)
        return parsed if parsed["method"] != "none" else NO_METHOD

    def resolve(self, from_addr):
        """Authoritative unsubscribe method for a sender = the newest message's List-Unsubscribe: from what's
        stored, else from the server for the newest message alone, else (a sweep took the sender's mail, and
        this is a second try) from what was last read. Returns (method info, the accounts it wrote to)."""
        rows, accts, info = self._mail.unsub_stored(from_addr)
        if info is None and rows:
            info = self._from_server(rows[0])
        if info is not None:
            self._mail.keep_unsub(from_addr, info, accts)
            return info, accts
        kept = self._mail.kept_unsub(from_addr)
        if kept is not None:
            return mime.list_unsub_stored(kept["raw"]), json.loads(kept["accounts"])
        return NO_METHOD, accts

    def _mailto(self, info, acct_emails):
        """Send the mailto: unsubscribe from EACH affected account (the list identifies you by your From).
        Host/creds come from config keyed by the account email — never the request → no SMTP-SSRF."""
        to = info.get("mailto_to") or ""
        if not re.fullmatch(r"[^\s@<>,;:\"()\[\]\\]+@[^\s@<>,;:\"()\[\]\\]+", to):
            return False                                     # one plain address, or nothing is sent
        subj = re.sub(r"[\r\n]", " ", (info.get("mailto_subject") or "unsubscribe"))[:200]
        text = re.sub(r"[\r\n]{3,}", "\n\n", (info.get("mailto_body") or "Please unsubscribe me."))[:1000]
        sent = 0
        for em in acct_emails:
            try:
                m = email.message.EmailMessage()
                m["From"] = em
                m["To"] = to
                m["Subject"] = subj
                m["Date"] = email.utils.format_datetime(datetime.now(timezone.utc))
                m["Message-ID"] = email.utils.make_msgid(domain=em.rpartition("@")[2] or None)
                m["Auto-Submitted"] = "auto-generated"
                m.set_content(text)
                if self._send.send(em, m.as_bytes(policy=email.policy.SMTP), [to]):   # CRLF, as SMTP wants
                    sent += 1
            except Exception:
                pass                                         # a removed or stale account: skip it, not the cleanup
        return sent > 0

    def attempt(self, from_addr, resolved):
        """Try the way `resolved` (what resolve gave) names. Returns (status, method, http status, a URL for
        the person to open)."""
        status, http_status, browser_url, method = "skipped", None, None, "none"
        if not profile.writes_allowed():
            return "read_only", method, http_status, browser_url   # sends nothing outside; not latched, so retryable
        key = "unsub:" + from_addr
        prior = self._mail.attempt(key)
        if prior and prior["status"] in TERMINAL:      # already done → never re-fire
            status, method = prior["status"], prior["method"]
            if method == "http-page":
                browser_url = resolved[0].get("https_uri")
            return status, method, http_status, browser_url
        info, accts = resolved
        method = info["method"]
        if method == "one-click":
            rate_gate()
            ok, http_status, _reason = self._http.one_click_post(info["https_uri"])
            status = "unsubscribed_oneclick" if ok else "failed"
        elif method == "mailto":
            rate_gate()
            status = "mailto_sent" if self._mailto(info, accts) else "failed"
        elif method == "http-page":
            status, browser_url = "needs_browser", info.get("https_uri")
        else:
            status = "no_method"
        if status in TERMINAL:                         # record ONLY terminal success; leave failures retryable
            self._mail.record_attempt(from_addr, method, status, http_status, key, accts)
        return status, method, http_status, browser_url
