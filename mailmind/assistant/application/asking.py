"""What a request asks of the AI: its fields read, its account checked, and which use case answers."""
import time
from collections.abc import Mapping
from typing import Any

from mailmind.assistant.application.ask import Asker
from mailmind.assistant.application.brief import Briefs
from mailmind.assistant.application.context import lone_key
from mailmind.assistant.application.ports import Mail, Result
from mailmind.assistant.application.review import Reviews
from mailmind.assistant.application.rewrite import Rewriter
from mailmind.assistant.application.thread_digest import ThreadDigest
from mailmind.assistant.domain import brief
from mailmind.kernel.errors import NotFound, Refused
from mailmind.kernel.ids import Pk
from mailmind.kernel.parsed import is_list, is_object, required_int
from mailmind.platform.db.uow import UnitOfWork


class Asking:
    def __init__(self, threads: ThreadDigest, briefs: Briefs, reviews: Reviews, rewriter: Rewriter, asker: Asker, uow: UnitOfWork,
                 mail: Mail):
        self._threads = threads
        self._briefs = briefs
        self._reviews = reviews
        self._rewriter = rewriter
        self._asker = asker
        self._uow = uow
        self._mail = mail

    def summarize(self, body: Mapping[str, Any]) -> Result | tuple[Result, int]:
        """On-demand thread/message summary via claude."""
        scope = "thread"
        account = body.get("account")
        thread_id = body.get("thread_id")
        if body.get("pk") is not None:
            with self._uow.read() as c:
                row = self._mail.get_message(c, Pk(required_int(body["pk"])))
            if not row:
                raise NotFound("not found")
            account = row["account"]
            thread_id = row["thread_id"] or lone_key(row)
            scope = "message" if not row["thread_id"] else "thread"
        if not account or account not in {a["email"] for a in self._mail.accounts()} or not thread_id:
            raise Refused()
        return self._threads.summarize(account, thread_id, scope, force=bool(body.get("force")))

    def _one(self, qs_or_body: object, name: str) -> Any:
        """One field of a parsed query string (a list per name) or of a JSON body, or None."""
        v = qs_or_body.get(name) if is_object(qs_or_body) else None
        return (v[0] if v else None) if is_list(v) else v

    def _enabled(self) -> set[str]:
        return {a["email"] for a in self._mail.accounts()}

    def _accounts(self, qs_or_body: object) -> set[str]:
        """The enabled accounts, or the one a passed ?account=/account names."""
        enabled = self._enabled()
        acct = self._one(qs_or_body, "account") or None
        return {acct} if (acct and acct in enabled) else enabled

    def briefing(self, force: bool = False, generate: bool = True) -> Result:
        """The synthesized 'Today' card. GET only reads what is stored (generate=False); POST
        /api/briefing writes it once, and /api/briefing/refresh again (force)."""
        return self._briefs.build(self._enabled(), force=force, generate=generate)

    def review(self, force: bool = False, generate: bool = True) -> Result:
        """The Weekly Review — retrospective over the last 7 days. GET only reads what is stored;
        POST /api/review writes it once, and /api/review/refresh again (force)."""
        return self._reviews.build(self._enabled(), force=force, generate=generate)

    def delta(self, qs_or_body: object, generate: bool = True) -> Result:
        """The 'since you last looked' delta digest. GET only reads what is stored; POST writes it.
        `since`=epoch seconds (clamped to a 24h floor so a stale client watermark can never scan all
        history)."""
        accts = self._accounts(qs_or_body)
        try:
            since = int(float(self._one(qs_or_body, "since") or 0))
        except Exception:
            since = 0
        return self._briefs.build_delta(accts, brief.delta_since(since, int(time.time())), generate=generate)

    def rewrite(self, body: Mapping[str, Any]) -> Result:
        """Rewrite/tone/translate the composer text via claude."""
        return self._rewriter.rewrite(str(body.get("text") or ""), str(body.get("op") or ""),
                               lang=str(body.get("lang") or "English"))

    def ask(self, body: Mapping[str, Any]) -> Result:
        """Ask-your-inbox: answer a natural-language question about the user's mail via claude.
        Pure read (FTS retrieve → bounded claude read), no mutation/SMTP — like summarize/
        rewrite. Account is validated before any mail is touched."""
        account = (body.get("account") or "all").strip() or "all"
        question = str(body.get("question") or "").strip()[:8000]
        if account != "all" and account not in {a["email"] for a in self._mail.accounts()}:
            raise Refused()
        if not question:
            raise Refused("question required")
        return self._asker.ask(account, question)
