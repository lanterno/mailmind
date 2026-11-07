"""What the page asks of the Mailbox: its lists, a message opened, filing and the sync."""
import re
import threading
import urllib.parse

from mailmind.compose.api import MAX_ATTACH_BYTES
from mailmind.http.routing import ANSWERED, Routes
from mailmind.kernel.errors import NotFound, Refused
from mailmind.kernel.parsed import required_int
from mailmind.platform import perimeter

_BODY_DOCUMENT = re.compile(r"/api/message/([0-9]{1,18})/body\.html")


def _safe_filename(name):
    """Never trust an email-supplied filename: strip path + control/quote/CRLF chars."""
    name = (name or "attachment")
    name = name.split("/")[-1].split("\\")[-1]
    name = re.sub(r'[\x00-\x1f\x7f"\r\n]', "_", name).strip(" .") or "attachment"
    return name[:255]


def _attachment(h, mailbox, pk, part_id):
    if not re.match(r"^[0-9]+$", part_id or ""):     # only the part ids mime gives
        raise Refused("bad part id")
    found = mailbox.bodies.attachment(pk, part_id)
    if "error" in found:
        raise NotFound(found["error"])
    data = found["data"]
    if len(data) > MAX_ATTACH_BYTES:
        return h._json({"ok": False, "error": "too large"}, 413)
    ctype = (found["content_type"] or "application/octet-stream").split(";")[0].strip()
    # never inline-render active types from the top frame; force download regardless
    fn = _safe_filename(found["filename"])
    disp = "attachment; filename*=UTF-8''" + urllib.parse.quote(fn, safe="")
    h._send_raw(200, data, ctype, {"Content-Disposition": disp})


def routes(mailbox):
    r = Routes()

    @r.get("/api/digest")
    def digest(req):
        return mailbox.digest.answer(req.query)

    @r.get("/api/stats")
    def stats(req):
        return mailbox.stats.live()

    @r.get("/api/folders")
    def folders(req):
        account = req.arg("account")
        if not account:
            raise Refused("account required", folders=[])
        return mailbox.folders.of_account(account)

    @r.get("/api/messages")
    def messages(req):
        return mailbox.queries.messages_in(req.arg("account"), req.arg("folder", "INBOX"),
                                           req.int_arg("offset", 0), req.int_arg("limit", 80),
                                           group=(req.arg("group", "") == "thread"))

    @r.get("/api/inboxes")
    def inboxes(req):
        return mailbox.queries.all_inboxes(req.int_arg("offset", 0), req.int_arg("limit", 80),
                                           group=(req.arg("group", "") == "thread"))

    @r.get("/api/waiting-on-others")
    def waiting_on_others(req):
        return mailbox.queries.waiting_on_others()

    @r.get("/api/thread")
    def thread(req):
        return mailbox.queries.thread_json(req.arg("account"), req.arg("thread_id"))

    @r.get("/api/pending")
    def pending(req):
        return mailbox.commands.list_open()

    @r.matching("GET", lambda path: path.startswith("/api/message/") and "/attachment/" in path)
    def attachment(req):
        parts = req.path.split("/")     # ['', 'api', 'message', '<pk>', 'attachment', '<part_id>']
        _attachment(req.handler, mailbox, required_int(parts[3]), parts[5])
        return ANSWERED

    @r.matching("GET", lambda path: path.startswith("/api/message/") and path.endswith("/body"))
    def body(req):
        return mailbox.bodies.message_body(required_int(req.path.split("/")[3]), prefetch=(req.arg("prefetch", "0") == "1"))

    @r.matching("GET", _BODY_DOCUMENT.fullmatch)
    def body_document(req):
        load_images = req.arg("images", "0") == "1"
        code, doc = mailbox.bodies.as_document(int(req.path.split("/")[3]), req.arg("key", ""), load_images)
        req.handler._send(code, doc.encode("utf-8", "replace"), "text/html; charset=utf-8",
                          perimeter.mail_frame_headers(load_images))
        return ANSWERED

    @r.post("/api/pending/retry")
    def pending_retry(req):
        return mailbox.filing.do_pending_retry(required_int(req.body.get("id")))

    @r.post("/api/pending/discard")
    def pending_discard(req):
        return mailbox.filing.do_pending_discard(required_int(req.body.get("id")))

    @r.post("/api/trash")
    def trash(req):
        return mailbox.filing.do_trash(req.body)

    @r.post("/api/thread/archive")
    @r.post("/api/thread/trash")
    def thread_file(req):
        return mailbox.filing.do_thread_file(req.path, req.body)

    @r.post("/api/bulk/file")
    def bulk_file(req):
        return mailbox.filing.file_many(req.body)

    @r.post("/api/flag")
    @r.post("/api/seen")
    @r.post("/api/archive")
    @r.post("/api/junk")
    def write(req):
        return mailbox.filing.do_write(req.path, req.body)

    @r.post("/api/undo")
    def undo(req):
        return mailbox.undo.undo_last()

    @r.post("/api/refresh")
    def refresh(req):
        threading.Thread(target=mailbox.syncing.sync_once, daemon=True).start()
        return {"ok": True, "msg": "sync started"}

    @r.post("/api/reconcile")
    def reconcile(req):
        return mailbox.reconciler.do_reconcile(req.body.get("account", "all"), bool(req.body.get("dry_run", False)),
                                               force=req.body.get("force") is True)

    @r.post("/api/sync-retry-account")
    def sync_retry(req):
        return mailbox.syncing.do_sync_retry(req.body)

    return r
