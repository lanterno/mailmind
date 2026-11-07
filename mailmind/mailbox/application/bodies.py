"""A message opened: its body from the server if it isn't stored, sanitized for the reader's sandboxed frame."""
import base64
import re
from typing import Any

from mailmind.content import frame, mime, sanitize
from mailmind.content.mime import Parsed
from mailmind.mailbox.application.ports import Reader, StoredMail
from mailmind.mailbox.application.sync import Ingest
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger
from mailmind.platform.workers import gates

log = get_logger("mailbox")

_CID_IMG = re.compile(r"""src\s*=\s*(['"])cid:([^'"]+)\1""", re.I)
_RASTER = {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"}
_CID_BUDGET = 3 << 20   # cap total inline-image bytes embedded into the iframe


class Bodies:
    def __init__(self, uow: UnitOfWork, store: StoredMail, ingest: Ingest, reader: Reader):
        self._uow = uow
        self._store = store
        self._ingest = ingest
        self._reader = reader

    def _resolve_cids(self, pk: int, safe_html: str) -> str:
        """Rewrite src="cid:<id>" -> a data: URI for embedded RASTER images only, so the
        sandboxed iframe (CSP img-src allows data:) renders logos without any network.
        Active types (svg/html) are never embedded; oversized sets are left as cid (broken)."""
        with self._uow.read() as c:
            raw = self._store.get_body(c, pk)
        if not raw:
            return safe_html
        parts = mime.inline_parts(raw)        # {content_id: (content_type, bytes)}
        if not parts:
            return safe_html
        used = [0]

        def sub(m: re.Match[str]) -> str:
            cid = m.group(2).strip().strip("<>")
            ent = parts.get(cid)
            if not ent:
                return m.group(0)
            ctype, data = ent
            if ctype not in _RASTER or used[0] + len(data) > _CID_BUDGET:
                return m.group(0)
            used[0] += len(data)
            b64 = base64.b64encode(data).decode("ascii")
            return f'src={m.group(1)}data:{ctype};base64,{b64}{m.group(1)}'
        return _CID_IMG.sub(sub, safe_html)

    def _safe_html(self, pk: int, parsed: Parsed, load_images: bool) -> tuple[str | None, dict[str, int]]:
        """A parsed message's HTML as its frame may show it, None if it has none, and what was left out."""
        html = parsed.get("html")
        if html and len(html) > 2_000_000:        # hard body cap before sanitize (DoS)
            html = html[:2_000_000]
        safe_html, info = (sanitize.sanitize(html, load_images=load_images)
                           if html else (None, {"blocked_images": 0, "blocked_css_urls": 0}))
        if safe_html and "cid:" in safe_html:
            safe_html = self._resolve_cids(pk, safe_html)   # embed inline images as data: (raster only)
        return safe_html, info

    def message_body(self, pk: int, prefetch: bool = False) -> dict[str, Any]:
        with self._uow.read() as c:
            row = self._store.get_message(c, pk)
        if not row:
            return {"ok": False, "error": "not found"}
        if prefetch:
            # background cache-warm: NEVER block a foreground op. If the lock is busy or the
            # body still needs a (slow) IMAP fetch, just skip — the real open will fetch later.
            if row["body_fetched"]:
                return {"ok": True, "prefetched": True}     # already warm
            try:
                gates.run(row["account"], lambda: self._ingest.fetch_body(pk),
                          priority=gates.Priority.MAINTENANCE, wait=False)
            except gates.Busy:
                return {"ok": False, "busy": True}
            return {"ok": True, "prefetched": True}
        parsed = gates.run(row["account"], lambda: self._ingest.fetch_body(pk), priority=gates.Priority.INTERACTIVE)
        if parsed is None:
            return {"ok": False, "error": "not found"}
        safe_html, info = self._safe_html(pk, parsed, False)
        invite, invite_part = self._reader.invite_of(pk, parsed)
        return {
            "ok": True, "pk": pk, "key": row["message_key"], "html": safe_html, "text": parsed.get("text"),
            "has_html": bool(parsed.get("html")), "attachments": parsed["attachments"],
            "invite": invite, "invite_part": invite_part,
            "blocked_images": info.get("blocked_images", 0),
            "blocked_css_urls": info.get("blocked_css_urls", 0),
            "to": parsed.get("to"), "cc": parsed.get("cc"), "date": parsed.get("date"),
            "reply_to": parsed.get("reply_to"),
            "from_name": row["from_name"], "from_addr": row["from_addr"],
            "subject": row["subject"], "account": row["account"], "thread_id": row["thread_id"],
            # threading identifiers (from the stored row) so the client can build a reply
            "message_id": row["message_id"], "in_reply_to": row["in_reply_to"],
            "references": row["refs"],
            **self._reader.draft_info(row),
        }

    def as_document(self, pk: int, key: str, load_images: bool) -> tuple[int, str]:
        """(status, the document a message's frame loads). A frame shows what it is sent, and a browser's own error page
        for an answer it may not frame, so a failure comes as a document too. Asks nothing of the AI.

        `key` must be the key of the row `pk` names: a pk can come to name another message (reconcile renumbers), and the
        bar above the frame speaks for the message the page read, so a frame never shows another's body under it."""
        try:
            with self._uow.read() as c:
                row = self._store.get_message(c, pk)
            parsed = None
            if row and str(row["message_key"]) == key:
                parsed = gates.run(row["account"], lambda: self._ingest.fetch_body(pk),
                                   priority=gates.Priority.INTERACTIVE)
            if parsed is None:
                return 404, frame.of_text("This message isn’t here any more.")
            if not parsed.get("html"):
                return 200, frame.of_text(parsed.get("text"))
            return 200, frame.of_html(self._safe_html(pk, parsed, load_images)[0])
        except Exception as e:                # scrub: log the detail, never echo it to the client
            log.error("message %(id)s: its document failed", {"id": pk}, exc_info=e)
            return 500, frame.of_text("Couldn’t load this message.")

    def attachment(self, pk: int, part_id: str) -> dict[str, Any]:
        """A part of a message: its bytes, content type and filename, or the error that says why not."""
        with self._uow.read() as c:
            row = self._store.get_message(c, pk)
        if not row:
            return {"error": "not found"}
        gates.run(row["account"], lambda: self._ingest.fetch_body(pk),   # ensure body + attachment metadata exist
                  priority=gates.Priority.INTERACTIVE)
        with self._uow.read() as c:
            att = self._store.attachment_of(c, pk, part_id)
            raw = self._store.get_body(c, pk)
        if not att or not raw:
            return {"error": "no such part"}
        data, _ = mime.extract_part(raw, part_id)
        if data is None:
            return {"error": "no such part"}
        return {"data": data, "content_type": att["content_type"], "filename": att["filename"]}
