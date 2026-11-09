"""A message opened: its body from the server if it isn't stored, sanitized for the reader's sandboxed frame."""
from typing import Any

from mailmind.content import frame, mime, sanitize
from mailmind.content.mime import Parsed
from mailmind.kernel.errors import NotFound
from mailmind.kernel.ids import FolderName, MessageKey
from mailmind.kernel.parsed import as_int
from mailmind.mailbox.application.ports import Reader, StoredMail
from mailmind.mailbox.application.sync import Ingest
from mailmind.mailbox.domain import inline_images
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger
from mailmind.platform.workers import gates

log = get_logger("mailbox")

_GONE = "This message isn’t here any more."
_NO_PREVIEW = "This file can’t be previewed. Download it to see it."
PREVIEW_MAX_BYTES = 10 << 20   # what a preview holds of one image, as its document carries the bytes



def _as_number(text: str) -> int | None:
    """A number a query gave as text, as the body route's schema reads one, or None for what isn't a whole number."""
    return as_int(text) if text.isascii() and text.isdigit() else None

class Bodies:
    def __init__(self, uow: UnitOfWork, store: StoredMail, ingest: Ingest, reader: Reader):
        self._uow = uow
        self._store = store
        self._ingest = ingest
        self._reader = reader

    def _resolve_cids(self, pk: int, safe_html: str) -> str:
        """The inline images of the message `pk`, as far as `domain.inline_images` may embed them."""
        with self._uow.read() as c:
            raw = self._store.get_body(c, pk)
        if not raw:
            return safe_html
        parts = mime.inline_parts(raw)
        return inline_images.embed(safe_html, parts) if parts else safe_html

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
        """A message opened, or for a `prefetch` whether its body is stored now. Raises NotFound for one that has gone, or
        whose body the server no longer has."""
        with self._uow.read() as c:
            row = self._store.get_message(c, pk)
        if not row:
            raise NotFound("not found")
        if prefetch:
            # background cache-warm: NEVER block a foreground op. If the lock is busy or the
            # body still needs a (slow) IMAP fetch, just skip — the real open will fetch later.
            if row["body_fetched"]:
                return {"ok": True, "prefetched": True}     # already warm
            try:
                gates.run(row["account"], lambda: self._ingest.fetch_body(pk),
                          priority=gates.Priority.MAINTENANCE, wait=False)
            except gates.Busy:                   # it gave way: nothing went wrong, and the open will fetch it
                return {"ok": True, "prefetched": False}
            return {"ok": True, "prefetched": True}
        parsed = gates.run(row["account"], lambda: self._ingest.fetch_body(pk), priority=gates.Priority.INTERACTIVE)
        if parsed is None:
            raise NotFound("not found")
        safe_html, info = self._safe_html(pk, parsed, False)
        invite, invite_part = self._reader.invite_of(pk, parsed)
        return {
            "ok": True, "key": row["message_key"], "folder": row["folder"], "uid": row["uid"], "html": safe_html,
            "text": parsed.get("text"),
            "has_html": bool(parsed.get("html")), "attachments": parsed["attachments"],
            "invite": invite, "invite_part": invite_part,
            "blocked_images": info.get("blocked_images", 0),
            "blocked_css_urls": info.get("blocked_css_urls", 0),
            "to": parsed.get("to"), "cc": parsed.get("cc"), "date": parsed.get("date"),
            "reply_to": parsed.get("reply_to"),
            "from_name": row["from_name"], "from_addr": row["from_addr"],
            "sender": self._reader.sender_facts(row["from_addr"]),
            "subject": row["subject"], "account": row["account"], "thread_id": row["thread_id"],
            # threading identifiers (from the stored row) so the client can build a reply
            "message_id": row["message_id"], "in_reply_to": row["in_reply_to"],
            "references": row["refs"],
            **self._reader.draft_info(row),
        }

    def as_document(self, pk: int, key: str, load_images: bool, uid: int | None = None) -> tuple[int, str]:
        """(status, the document a message's frame loads). A frame shows what it is sent, and a browser's own error page
        for an answer it may not frame, so a failure comes as a document too. Asks nothing of the AI.

        `key` must be the key of the row `pk` names, and `uid`, where given, its uid: a pk can come to name another message
        (reconcile renumbers), and the bar above the frame speaks for the copy the page read, so a frame never shows another's
        body under it."""
        try:
            with self._uow.read() as c:
                row = self._store.get_message(c, pk)
            parsed = None
            if row and str(row["message_key"]) == key and (uid is None or row["uid"] == uid):
                parsed = gates.run(row["account"], lambda: self._ingest.fetch_body(pk),
                                   priority=gates.Priority.INTERACTIVE)
            if parsed is None:
                return 404, frame.of_text(_GONE)
            if not parsed.get("html"):
                return 200, frame.of_text(parsed.get("text"))
            return 200, frame.of_html(self._safe_html(pk, parsed, load_images)[0])
        except Exception as e:                # scrub: log the detail, never echo it to the client
            log.error("message %(id)s: its document failed", {"id": pk}, exc_info=e)
            return 500, frame.of_text("Couldn’t load this message.")

    def copy_document(self, key: str, folder: str, uid: str, load_images: bool) -> tuple[int, str]:
        """(status, the document the frame of the copy `key`, `folder` and `uid` name loads), as `as_document` gives it, and
        as the reader's body route reads them (`StoredMail.copy_pk`): a uid left out ("") is the only copy there. A key,
        folder and uid that name no copy now, whatever they say, are a document saying so."""
        try:
            pk, number, at = self._copy_named(key, folder, uid)
        except Exception as e:                # scrub: log the detail, never echo it to the client
            log.error("a copy's document failed", exc_info=e)
            return 500, frame.of_text("Couldn’t load this message.")
        if pk is None:
            return 404, frame.of_text(_GONE)
        return self.as_document(pk, str(number), load_images, at)     # the key as read, so "007" names what 7 does

    def _copy_named(self, key: str, folder: str, uid: str) -> tuple[int | None, int | None, int | None]:
        """(the row of the copy these name, its key as read, its uid as read). A uid left out ("") is the only copy there."""
        number = _as_number(key)
        at = None if uid == "" else _as_number(uid)
        with self._uow.read() as c:
            pk = (self._store.copy_pk(c, MessageKey(number), FolderName(folder), at)
                  if number is not None and (uid == "" or at is not None) else None)
        return pk, number, at

    def preview_document(self, key: str, folder: str, uid: str, part_id: str) -> tuple[int, str]:
        """(status, the document the preview of a part of the copy these name loads): the image it holds, of the kinds
        `frame.image_type` finds in its bytes, whatever its sender called it, and otherwise a document saying why not. A frame
        shows what it is sent, so a failure comes as a document too."""
        try:
            pk, _, _ = self._copy_named(key, folder, uid)
            if pk is None:
                return 404, frame.of_text(_NO_PREVIEW)
            found = self.attachment(pk, part_id)
            if "error" in found:
                return 404, frame.of_text(_NO_PREVIEW)
            media_type = frame.image_type(found["data"])
            if media_type is None or len(found["data"]) > PREVIEW_MAX_BYTES:
                return 200, frame.of_text(_NO_PREVIEW)
            return 200, frame.of_image(found["data"], media_type)
        except Exception as e:                # scrub: log the detail, never echo it to the client
            log.error("a preview failed", exc_info=e)
            return 500, frame.of_text("Couldn’t load this file.")

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
