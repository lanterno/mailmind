"""What counts as a document, which kind, and what the Document Hub shows of them. Calendar .ics, cryptographic signatures
and key blobs are excluded as non-documents."""
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, get_args
from urllib.parse import urlencode

from mailmind.kernel.row import Row

# Which kind of file a document is: an answer names it so, and the page's words for each are its own.
type DocumentKind = Literal["pdf", "image", "doc", "sheet", "slide", "archive", "text", "other"]

# What the files can be shown in sections of. Left out, they are one list, the newest first.
type DocumentGrouping = Literal["sender", "type"]

EXCLUDE = ("text/calendar", "application/ics", "pkcs7", "pgp-signature", "pgp-keys",
           "x-pkcs7", "application/x-pkcs7-signature")


_IMAGE_TYPES = ("image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp")
_IMAGE_EXTENSIONS = ("png", "jpg", "jpeg", "gif", "webp")


def is_document(ct: str | None) -> bool:
    ct = (ct or "").lower()
    return not any(x in ct for x in EXCLUDE)


def kind_of(ct: str | None, fn: str | None) -> DocumentKind:
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


def previewable(ct: str | None, fn: str | None) -> bool:
    """Whether to offer a preview of a file: what it says it is, or by its name where its type says nothing. What its bytes are
    decides what the preview shows (`frame.image_type`)."""
    ct = (ct or "").lower().split(";")[0].strip()
    fn = (fn or "").lower()
    if ct in ("", "application/octet-stream"):
        return "." in fn and fn.rsplit(".", 1)[-1] in _IMAGE_EXTENSIONS
    return ct in _IMAGE_TYPES


@dataclass(frozen=True)
class Asked:
    limit: int = 400
    offset: int = 0
    kind: str = ""          # one kind, or every kind
    needle: str = ""        # lowercase: words in its name, its sender or its subject
    group: str = ""         # a DocumentGrouping, or none


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
    return Asked(limit, offset, (qs.get("kind") or [""])[0], (qs.get("q") or [""])[0].strip().lower(), (qs.get("group") or [""])[0])


def same_file(r: Row) -> tuple[Any, ...]:
    """What names one physical file. The identical email synced to two accounts or folders has another pk, so it goes by
    Message-ID, size and name, and by pk where there is no Message-ID, so unrelated files of one name aren't merged."""
    name = (r["filename"] or "").lower()
    return (r["message_id"], r["size"], name) if r["message_id"] else (r["pk"], name)


def holds(r: Row, needle: str) -> bool:
    return not needle or needle in " ".join(r[k] or "" for k in ("filename", "from_name", "from_addr", "subject")).lower()


def _section_of(r: Row, kind: DocumentKind, by: str) -> tuple[str, str]:
    """(what a file's section goes by, what it's called). A sender goes by their address, so one person writing under two names
    is one section, called what their newest file says."""
    if by == "type":
        return kind, kind
    name = r["from_name"] or r["from_addr"] or ""
    return (r["from_addr"] or name).lower(), name


def _in_sections(matches: list[tuple[Row, DocumentKind]], by: str) -> tuple[list[tuple[Row, DocumentKind]], list[dict[str, Any]]]:
    """`matches`, each section's together, and the sections in the same order: by type the kinds' own order, by sender whoever
    sent the newest file first, which leaves the newest file at the top. Inside a section they stay as they came, the newest first."""
    sections: dict[str, dict[str, Any]] = {}
    for r, kind in matches:
        at, name = _section_of(r, kind, by)
        sections.setdefault(at, {"id": at, "name": name, "count": 0})["count"] += 1
    order = list(get_args(DocumentKind.__value__)) if by == "type" else list(sections)
    rank = {at: i for i, at in enumerate(order)}
    return (sorted(matches, key=lambda m: rank[_section_of(m[0], m[1], by)[0]]),
            sorted(sections.values(), key=lambda s: rank[s["id"]]))


def page(rows: Iterable[Row], want: Asked, when: Callable[[str | None], str]) -> dict[str, Any]:
    """Real attachments in the order `rows` holds them, or in sections when asked for: the `limit` from `offset` of those that
    match (`matched` counts them), with per-kind facet counts of them all. `when` says a date as the list does."""
    matches: list[tuple[Row, DocumentKind]] = []
    facets: dict[str, int] = {}
    seen: set[tuple[Any, ...]] = set()
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
        matches.append((r, kind))
    sections: list[dict[str, Any]] = []
    if want.group:
        matches, sections = _in_sections(matches, want.group)
    docs: list[dict[str, Any]] = []
    for r, kind in matches[want.offset:want.offset + want.limit]:
        names = urlencode({"key": r["message_key"], "folder": r["folder"], "uid": r["uid"], "part_id": r["part_id"]})
        docs.append({"key": r["message_key"], "folder": r["folder"], "uid": r["uid"], "part_id": r["part_id"],
                     "filename": r["filename"], "kind": kind,
                     "size": r["size"] or 0, "account": r["account"],
                     "from": r["from_name"] or r["from_addr"] or "", "subject": r["subject"] or "(no subject)",
                     "when": when(r["date_iso"]),
                     "url": "/api/message/attachment?" + names,
                     "preview": ("/api/message/attachment/preview.html?" + names
                                 if previewable(r["content_type"], r["filename"]) else None),
                     "group": _section_of(r, kind, want.group)[0] if want.group else ""})
    return {"ok": True, "total": sum(facets.values()), "matched": len(matches), "shown": len(docs), "facets": facets,
            "groups": sections, "docs": docs}
