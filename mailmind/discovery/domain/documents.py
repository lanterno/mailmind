"""What counts as a document, which kind, and what the Document Hub shows of them. Calendar .ics, cryptographic signatures
and key blobs are excluded as non-documents."""
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from mailmind.kernel.row import Row

EXCLUDE = ("text/calendar", "application/ics", "pkcs7", "pgp-signature", "pgp-keys",
           "x-pkcs7", "application/x-pkcs7-signature")


def is_document(ct: str | None) -> bool:
    ct = (ct or "").lower()
    return not any(x in ct for x in EXCLUDE)


def kind_of(ct: str | None, fn: str | None) -> str:
    ct = (ct or "").lower(); fn = (fn or "").lower()
    ext = fn.rsplit(".", 1)[-1] if "." in fn else ""
    if "pdf" in ct or ext == "pdf":
        return "pdf"
    if ct.startswith("image/") or ext in ("png", "jpg", "jpeg", "gif", "webp", "heic", "svg", "bmp", "tiff"):
        return "image"
    if ext in ("doc", "docx", "odt", "rtf", "pages") or "msword" in ct or "wordprocessingml" in ct or "opendocument.text" in ct:
        return "doc"
    if ext in ("xls", "xlsx", "ods", "csv", "numbers", "tsv") or "spreadsheet" in ct or "ms-excel" in ct or ct == "text/csv":
        return "sheet"
    if ext in ("ppt", "pptx", "odp", "key") or "presentation" in ct or "powerpoint" in ct:
        return "slide"
    if ext in ("zip", "rar", "7z", "tar", "gz", "tgz") or "zip" in ct or "compress" in ct or "x-tar" in ct:
        return "archive"
    if ct.startswith("text/") or ext in ("txt", "md", "log", "json", "xml"):
        return "text"
    return "other"


@dataclass(frozen=True)
class Asked:
    limit: int = 400
    offset: int = 0
    kind: str = ""          # one kind, or every kind
    needle: str = ""        # lowercase: words in its name, its sender or its subject


def asked(qs: Mapping[str, list[str]]) -> Asked:
    """What a request asks for. A limit or an offset that won't read is the default, since a page in the wrong place harms no
    one."""
    try:
        limit = max(1, min(1000, int((qs.get("limit") or ["400"])[0])))
    except Exception:
        limit = 400
    try:
        offset = max(0, int((qs.get("offset") or ["0"])[0]))
    except Exception:
        offset = 0
    return Asked(limit, offset, (qs.get("kind") or [""])[0], (qs.get("q") or [""])[0].strip().lower())


def same_file(r: Row) -> tuple[Any, ...]:
    """What names one physical file. The identical email synced to two accounts or folders has another pk, so it goes by
    Message-ID, size and name, and by pk where there is no Message-ID, so unrelated files of one name aren't merged."""
    name = (r["filename"] or "").lower()
    return (r["message_id"], r["size"], name) if r["message_id"] else (r["pk"], name)


def holds(r: Row, needle: str) -> bool:
    return not needle or needle in " ".join(r[k] or "" for k in ("filename", "from_name", "from_addr", "subject")).lower()


def page(rows: Iterable[Row], want: Asked, when: Callable[[str | None], str]) -> dict[str, Any]:
    """Real attachments in the order `rows` holds them: the `limit` from `offset` of those that match (`matched` counts them),
    with per-kind facet counts of them all. `when` says a date as the list does."""
    docs: list[dict[str, Any]] = []
    facets: dict[str, int] = {}
    seen: set[tuple[Any, ...]] = set()
    matched = 0
    for r in rows:
        if not is_document(r["content_type"]):
            continue
        kind = kind_of(r["content_type"], r["filename"])
        key = same_file(r)
        if key in seen:
            continue
        seen.add(key)
        facets[kind] = facets.get(kind, 0) + 1
        if want.kind and kind != want.kind:
            continue
        if not holds(r, want.needle):
            continue
        matched += 1
        if matched <= want.offset or len(docs) >= want.limit:
            continue
        docs.append({"key": r["message_key"], "folder": r["folder"], "part_id": r["part_id"], "filename": r["filename"], "kind": kind,
                     "size": r["size"] or 0, "account": r["account"],
                     "from": r["from_name"] or r["from_addr"] or "", "subject": r["subject"] or "(no subject)",
                     "when": when(r["date_iso"]),
                     "url": "/api/message/attachment?" + urlencode({"key": r["message_key"], "folder": r["folder"],
                                                                     "part_id": r["part_id"]})})
    return {"ok": True, "total": sum(facets.values()), "matched": matched, "shown": len(docs), "facets": facets, "docs": docs}
