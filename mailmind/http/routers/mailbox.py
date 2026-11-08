"""What the page asks of the Mailbox: its lists, a message opened, filing and the sync."""
import re
import threading
import urllib.parse
from typing import Annotated

from fastapi import Query
from starlette.convertors import Convertor, register_url_convertor
from starlette.responses import Response

from mailmind.compose.api import MAX_ATTACH_BYTES
from mailmind.http import route
from mailmind.http.schemas.common import NoBody, NotJson, Number, answers
from mailmind.http.schemas.mailbox import (
    BodyArgs,
    BodyDocumentArgs,
    BulkFileBody,
    Digest,
    Done,
    Filed,
    FlagBody,
    FolderListArgs,
    FolderListing,
    Folders,
    FoldersArgs,
    FoldersRefused,
    InboxesListing,
    ListArgs,
    ManyFiled,
    MessageOpened,
    MoveBody,
    MutedListing,
    NamesAPending,
    NoMessage,
    NoThread,
    NotFiled,
    NothingToUndo,
    NotUndone,
    Pending,
    Prefetched,
    ReconcileBody,
    Reconciled,
    ReplyLaterListing,
    RowsFiled,
    SeenBody,
    SnoozedListing,
    Stats,
    SyncRetried,
    SyncRetryBody,
    SyncStarted,
    Thread,
    ThreadArgs,
    Undone,
    WaitingListing,
    WaitingOnOthers,
)
from mailmind.kernel.errors import NotFound, Refused
from mailmind.platform import perimeter


class _FramePk(Convertor[int]):
    """A pk as a frame's document takes one. What's anything else isn't that route, which could only answer it as a document."""
    regex = "[0-9]{1,18}"

    def convert(self, value):
        return int(value)

    def to_string(self, value):
        return str(value)


register_url_convertor("frame_pk", _FramePk())

_FILED = answers(Filed | NotFiled)
_THREAD_FILED = answers(ManyFiled | NotFiled)


def _safe_filename(name):
    """Never trust an email-supplied filename: strip path + control/quote/CRLF chars."""
    name = (name or "attachment")
    name = name.split("/")[-1].split("\\")[-1]
    name = re.sub(r'[\x00-\x1f\x7f"\r\n]', "_", name).strip(" .") or "attachment"
    return name[:255]


def router(mailbox):
    r = route.router()

    @r.get("/api/digest", responses=answers(Digest))
    def digest():
        return mailbox.digest.answer()

    @r.get("/api/stats", responses=answers(Stats))
    def stats():
        return mailbox.stats.live()

    @r.get("/api/folders", responses=answers(Folders, {400: FoldersRefused}))
    def folders(args: Annotated[FoldersArgs, Query()]):
        if not args.account:
            raise Refused("account required", folders=[])
        return mailbox.folders.of_account(args.account)

    @r.get("/api/messages", responses=answers(FolderListing | SnoozedListing | MutedListing | WaitingListing | ReplyLaterListing))
    def messages(args: Annotated[FolderListArgs, Query()]):
        return mailbox.queries.messages_in(args.account, args.folder, args.offset, args.limit, group=(args.group == "thread"))

    @r.get("/api/inboxes", responses=answers(InboxesListing))
    def inboxes(args: Annotated[ListArgs, Query()]):
        return mailbox.queries.all_inboxes(args.offset, args.limit, group=(args.group == "thread"))

    @r.get("/api/waiting-on-others", responses=answers(WaitingOnOthers))
    def waiting_on_others():
        return mailbox.queries.waiting_on_others()

    @r.get("/api/thread", responses=answers(Thread | NoThread))
    def thread(args: Annotated[ThreadArgs, Query()]):
        return mailbox.queries.thread_json(args.account, args.thread_id)

    @r.get("/api/pending", responses=answers(Pending))
    def pending():
        return mailbox.commands.list_open()

    @r.get("/api/message/{pk}/attachment/{part_id}", responses=answers(NotJson))
    def attachment(pk: Number, part_id: str):
        if not re.match(r"^[0-9]+$", part_id):     # only the part ids mime gives
            raise Refused("bad part id")
        found = mailbox.bodies.attachment(pk, part_id)
        if "error" in found:
            raise NotFound(found["error"])
        data = found["data"]
        if len(data) > MAX_ATTACH_BYTES:
            return route.problem(413, "too large")
        ctype = (found["content_type"] or "application/octet-stream").split(";")[0].strip()
        # never inline-render active types from the top frame; force download regardless
        fn = _safe_filename(found["filename"])
        disp = "attachment; filename*=UTF-8''" + urllib.parse.quote(fn, safe="")
        # belt-and-braces: even if a browser ignores Content-Disposition, sandbox neuters it
        return Response(data, headers={"Content-Type": ctype, "Content-Security-Policy": "default-src 'none'; sandbox",
                                       "Content-Disposition": disp})

    @r.get("/api/message/{pk}/body", responses=answers(MessageOpened | Prefetched | NoMessage))
    def body(pk: Number, args: Annotated[BodyArgs, Query()]):
        return mailbox.bodies.message_body(pk, prefetch=(args.prefetch == "1"))

    @r.get("/api/message/{pk:frame_pk}/body.html", responses=answers(NotJson))
    def body_document(pk: int, args: Annotated[BodyDocumentArgs, Query()]):
        load_images = args.images == "1"
        code, doc = mailbox.bodies.as_document(pk, args.key, load_images)
        return Response(doc.encode("utf-8", "replace"), code,
                        headers={"Content-Type": "text/html; charset=utf-8", **dict(perimeter.mail_frame_headers(load_images))})

    @r.post("/api/pending/retry", responses=answers(Done))
    def pending_retry(body: NamesAPending):
        return mailbox.filing.do_pending_retry(body.id)

    @r.post("/api/pending/discard", responses=answers(Done))
    def pending_discard(body: NamesAPending):
        return mailbox.filing.do_pending_discard(body.id)

    @r.post("/api/trash", responses=_FILED)
    def trash(body: MoveBody):
        return mailbox.filing.do_trash(body.model_dump(exclude_unset=True))

    @r.post("/api/thread/archive", responses=_THREAD_FILED)
    def thread_archive(body: MoveBody):
        return mailbox.filing.do_thread_file("/api/thread/archive", body.model_dump(exclude_unset=True))

    @r.post("/api/thread/trash", responses=_THREAD_FILED)
    def thread_trash(body: MoveBody):
        return mailbox.filing.do_thread_file("/api/thread/trash", body.model_dump(exclude_unset=True))

    @r.post("/api/bulk/file", responses=answers(RowsFiled))
    def bulk_file(body: BulkFileBody):
        return mailbox.filing.file_many(body.model_dump(exclude_unset=True))

    @r.post("/api/flag", responses=_FILED)
    def flag(body: FlagBody):
        return mailbox.filing.do_write("/api/flag", body.model_dump(exclude_unset=True))

    @r.post("/api/seen", responses=_FILED)
    def seen(body: SeenBody):
        return mailbox.filing.do_write("/api/seen", body.model_dump(exclude_unset=True))

    @r.post("/api/archive", responses=_FILED)
    def archive(body: MoveBody):
        return mailbox.filing.do_write("/api/archive", body.model_dump(exclude_unset=True))

    @r.post("/api/junk", responses=_FILED)
    def junk(body: MoveBody):
        return mailbox.filing.do_write("/api/junk", body.model_dump(exclude_unset=True))

    @r.post("/api/undo", responses=answers(Undone | NothingToUndo | NotUndone))
    def undo(body: NoBody):
        return mailbox.undo.undo_last()

    @r.post("/api/refresh", responses=answers(SyncStarted))
    def refresh(body: NoBody):
        threading.Thread(target=mailbox.syncing.sync_once, daemon=True).start()
        return {"ok": True, "msg": "sync started"}

    @r.post("/api/reconcile", responses=answers(Reconciled))
    def reconcile(body: ReconcileBody):
        return mailbox.reconciler.do_reconcile(body.account, bool(body.dry_run), force=body.force is True)

    @r.post("/api/sync-retry-account", responses=answers(SyncRetried))
    def sync_retry(body: SyncRetryBody):
        return mailbox.syncing.do_sync_retry(body.model_dump(exclude_unset=True))

    return r
