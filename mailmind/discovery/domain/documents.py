"""What counts as a document, and which kind. Calendar .ics, cryptographic signatures and key blobs are excluded as
non-documents."""
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
