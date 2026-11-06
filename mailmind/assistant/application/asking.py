"""What a request asks of the AI: its fields read, its account checked, and which use case answers. Each returns
(result, code)."""
import time

from mailmind.assistant.application.ask import Asker
from mailmind.assistant.application.brief import Briefs
from mailmind.assistant.application.ports import Mail
from mailmind.assistant.application.review import Reviews
from mailmind.assistant.application.rewrite import Rewriter
from mailmind.assistant.application.thread_digest import ThreadDigest


class Asking:
    def __init__(self, threads: ThreadDigest, briefs: Briefs, reviews: Reviews, rewriter: Rewriter, asker: Asker, connect, mail: Mail):
        self._threads = threads
        self._briefs = briefs
        self._reviews = reviews
        self._rewriter = rewriter
        self._asker = asker
        self._connect = connect
        self._mail = mail

    def summarize(self, body):
        """On-demand thread/message summary via claude. Returns (result, code)."""
        scope = "thread"
        account = body.get("account")
        thread_id = body.get("thread_id")
        if body.get("pk") is not None:
            c = self._connect()
            row = self._mail.get_message(c, int(body["pk"]))
            c.close()
            if not row:
                return {"ok": False, "error": "not found"}, 404
            account = row["account"]
            thread_id = row["thread_id"] or self._threads.lone_key(row)
            scope = "message" if not row["thread_id"] else "thread"
        if not account or account not in {a["email"] for a in self._mail.accounts()} or not thread_id:
            return {"ok": False, "error": "bad request"}, 400
        return self._threads.summarize(account, thread_id, scope, force=bool(body.get("force")))

    def _one(self, qs_or_body, name):
        """One field of a parsed query string (a list per name) or of a JSON body, or None."""
        v = qs_or_body.get(name) if isinstance(qs_or_body, dict) else None
        return (v[0] if v else None) if isinstance(v, list) else v

    def _accounts(self, qs_or_body):
        """The enabled accounts, or the one a passed ?account=/account names (matches the digest's
        per-account view), like digest_json's send_accounts."""
        enabled = {a["email"] for a in self._mail.accounts()}
        acct = self._one(qs_or_body, "account") or None
        return {acct} if (acct and acct in enabled) else enabled

    def briefing(self, qs_or_body, force=False, generate=True):
        """The synthesized 'Today' card. GET only reads what is stored (generate=False); POST
        /api/briefing writes it once, and /api/briefing/refresh again (force)."""
        return self._briefs.build(self._accounts(qs_or_body), force=force, generate=generate)

    def review(self, qs_or_body, force=False, generate=True):
        """The Weekly Review — retrospective over the last 7 days. GET only reads what is stored;
        POST /api/review writes it once, and /api/review/refresh again (force)."""
        return self._reviews.build(self._accounts(qs_or_body), force=force, generate=generate)

    def delta(self, qs_or_body, generate=True):
        """The 'since you last looked' delta digest. GET only reads what is stored; POST writes it.
        `since`=epoch seconds (clamped to a 24h floor so a stale client watermark can never scan all
        history)."""
        accts = self._accounts(qs_or_body)
        try:
            since = int(float(self._one(qs_or_body, "since") or 0))
        except Exception:
            since = 0
        now = int(time.time())
        floor = now - 24 * 3600
        floor -= floor % 600                  # quantize the 24h floor to a 10-min bucket so a stale watermark
                                              # yields a STABLE cache key (not a fresh claude call every second)
        since = min(max(since, floor), now)   # also cap at now (client/server clock-skew guard)
        return self._briefs.build_delta(accts, since, generate=generate)

    def rewrite(self, body):
        """Rewrite/tone/translate the composer text via claude. Returns (result, code)."""
        return self._rewriter.rewrite(str(body.get("text") or ""), str(body.get("op") or ""),
                               lang=str(body.get("lang") or "English"))

    def ask(self, body):
        """Ask-your-inbox: answer a natural-language question about the user's mail via claude.
        Pure read (FTS retrieve → bounded claude read), no mutation/SMTP — like summarize/
        rewrite. Account is validated before any mail is touched. Returns (result, code)."""
        account = (body.get("account") or "all").strip() or "all"
        question = str(body.get("question") or "").strip()[:8000]
        if account != "all" and account not in {a["email"] for a in self._mail.accounts()}:
            return {"ok": False, "error": "bad request"}, 400
        if not question:
            return {"ok": False, "error": "question required"}, 400
        return self._asker.ask(account, question)
